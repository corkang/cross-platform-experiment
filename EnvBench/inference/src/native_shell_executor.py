"""
AsyncBashExecutor와 동일한 인터페이스로,
Docker 대신 네이티브 OS에서 bash 명령을 실행하는 executor.

핵심 설계:
- Persistent bash session: long-lived bash 프로세스를 유지하여
  export, source, cd 등의 shell state가 command 간에 보존됨
- AsyncBashExecutor와 동일한 end marker + exit code 파싱 프로토콜 사용
- env_vars를 bash session 환경에 주입
- repository_workdir flag에 따라 초기 working directory 결정
- Windows에서는 Git Bash를 사용
- clean() 시 clear_repo semantics를 AsyncBashExecutor와 동일하게 유지
"""

import asyncio
import logging
import os
import platform
import uuid
from typing import Dict, List, Optional, Tuple

from env_setup_utils.repo_downloader import RepoDownloader
from inference.src.async_bash_executor import CommandExecutionResult


def _find_bash() -> str:
    """OS에 따라 bash 실행 파일 경로를 반환한다.

    - Linux/macOS: /bin/bash (또는 /usr/bin/bash)
    - Windows: Git Bash 경로 탐색
    """
    system = platform.system()
    if system in ("Linux", "Darwin"):
        for path in ("/bin/bash", "/usr/bin/bash"):
            if os.path.isfile(path):
                return path
        return "bash"

    if system == "Windows":
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
            r"C:\Program Files\Git\bin\bash.exe",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return "bash.exe"

    return "bash"


class NativeShellExecutor:
    DEFAULT_ERROR: str = "ERROR: Could not execute given command."

    def __init__(
        self,
        repository: str,
        revision: str,
        workdir: str,
        bash_path: str,
        bash_timeout: Optional[int] = None,
        bash_timeout_exit_code: int = -123,
        max_num_chars_bash_output: Optional[int] = None,
        error_message: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        repository_workdir: bool = True,
        clear_repo: bool = True,
        hf_name: str = "",
        output_dir: str = "",
        language: str = "",
    ):
        self.repository = repository
        self.revision = revision
        self.workdir = workdir
        self.bash_path = bash_path
        self.bash_timeout = bash_timeout
        self.bash_timeout_exit_code = bash_timeout_exit_code
        self.max_num_chars_bash_output = max_num_chars_bash_output
        self.error_message = error_message or self.DEFAULT_ERROR
        self.env_vars = env_vars or {}
        self.repository_workdir = repository_workdir
        self.clear_repo = clear_repo
        self.hf_name = hf_name
        self.output_dir = output_dir
        self.language = language
        self.commands_history: List[CommandExecutionResult] = []

        # persistent bash process (initialized in _start_session)
        self._process: Optional[asyncio.subprocess.Process] = None
        self._command_lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        repository: str,
        revision: str,
        hf_name: str,
        output_dir: str,
        language: str,
        # AsyncBashExecutor와 시그니처를 맞추기 위해 받는 파라미터
        image: str = "",
        command: Optional[str] = None,
        clear_repo: bool = True,
        error_message: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        repository_workdir: bool = True,
        container_start_timeout: int = 30,
        bash_timeout: Optional[int] = None,
        bash_timeout_exit_code: int = -123,
        max_num_chars_bash_output: Optional[int] = None,
    ) -> "NativeShellExecutor":
        # 레포 다운로드 (기존 로직 재사용)
        repo_downloader = RepoDownloader(
            hf_name=hf_name, output_dir=output_dir, language=language
        )
        is_downloaded = repo_downloader.download(
            repo_name=repository, commit_sha=revision
        )
        if not is_downloaded:
            raise ValueError(f"Unable to download repository {repository}@{revision}.")

        workdir = repo_downloader.get_repo_dir_path(
            repo_name=repository, commit_sha=revision
        )

        bash_path = _find_bash()
        logging.info(
            f"[{repository}@{revision}] NativeShellExecutor: using bash at '{bash_path}', workdir='{workdir}'"
        )

        executor = cls(
            repository=repository,
            revision=revision,
            workdir=workdir,
            bash_path=bash_path,
            bash_timeout=bash_timeout,
            bash_timeout_exit_code=bash_timeout_exit_code,
            max_num_chars_bash_output=max_num_chars_bash_output,
            error_message=error_message,
            env_vars=env_vars,
            repository_workdir=repository_workdir,
            clear_repo=clear_repo,
            hf_name=hf_name,
            output_dir=output_dir,
            language=language,
        )
        await executor._start_session()
        return executor

    async def _start_session(self) -> None:
        """Persistent bash session을 시작한다.

        - env_vars를 subprocess environment에 merge
        - repository_workdir=True이면 repo root에서 시작
        - repository_workdir=False이면 output_dir(repo 상위 디렉토리)에서 시작
          Docker executor에서 False일 때 container root(/)를 쓰는 것에 대응.
          native에서 cwd=None은 "현재 프로세스 cwd"가 되어 EnvBench repo root가
          유출되므로, 명시적으로 neutral directory를 지정한다.
        """
        env = os.environ.copy()
        env.update(self.env_vars)

        if self.repository_workdir:
            cwd = self.workdir
        else:
            # output_dir은 repo들이 다운로드되는 상위 디렉토리.
            # Docker의 "/"에 해당하는 neutral working directory로 사용한다.
            cwd = self.output_dir if self.output_dir else os.path.expanduser("~")

        self._process = await asyncio.create_subprocess_exec(
            self.bash_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        logging.info(
            f"[{self.repository}@{self.revision}] Persistent bash session started (pid={self._process.pid})."
        )

    async def _restart_session(self) -> None:
        """Bash session을 재시작하고, 성공했던 command들을 replay한다."""
        logging.warning(
            f"[{self.repository}@{self.revision}] Restarting bash session and replaying {len(self.commands_history)} commands."
        )
        await self._kill_process()
        await self._start_session()

        # 성공한 command들만 replay
        for cmd_record in self.commands_history:
            if cmd_record["exit_code"] == 0:
                output, exit_code = await self._execute_in_session(cmd_record["command"])
                if exit_code != 0:
                    logging.warning(
                        f"[{self.repository}@{self.revision}] Replay of '{cmd_record['command'][:80]}...' "
                        f"failed with exit code {exit_code}."
                    )

    async def _kill_process(self) -> None:
        """현재 bash process를 종료한다."""
        if self._process is not None:
            try:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

    async def _execute_in_session(self, command: str) -> Tuple[str, int]:
        """Persistent session에 command를 보내고 결과를 수집한다.

        AsyncBashExecutor와 동일한 end marker + exit code 프로토콜:
        1. command 실행
        2. exit code를 캡처하여 __EXIT_CODE__ marker로 출력
        3. __END_OF_COMMAND_{uuid}__ marker로 stdout 끝을 표시
        4. stderr도 별도 marker로 끝을 표시
        """
        assert self._process is not None and self._process.stdin is not None

        command_id = uuid.uuid4().hex
        end_marker = f"__END_OF_COMMAND_{command_id}__"
        stderr_end_marker = f"__END_OF_STDERR_{command_id}__"

        # stderr를 파일로 리다이렉트하면 복잡해지므로,
        # command를 wrapping하여 stderr도 stdout으로 보내되 구분 가능하게 한다.
        # 방식: command 실행 → exit code 캡처 → stderr separator → stdout end marker
        wrapped_command = (
            f'{command}\n'
            f'__exit_code_$?=$?\n'  # exit_code capture trick: avoid overwriting $?
            f'echo "__EXIT_CODE__ ${{__exit_code_$?:=$?}}"\n'
            f'echo "{end_marker}"\n'
        )

        # 더 안정적인 방식: subshell로 실행하고 exit code를 별도 캡처
        wrapped_command = (
            f'{command}\n'
            f'_ec=$?\n'
            f'echo "__EXIT_CODE__ $_ec"\n'
            f'echo "{end_marker}"\n'
            f'echo "{stderr_end_marker}" >&2\n'
        )

        self._process.stdin.write(wrapped_command.encode("utf-8"))
        await self._process.stdin.drain()

        # stdout에서 end marker까지 읽기
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        exit_code: Optional[int] = None
        timed_out = False

        async def _read_stdout():
            nonlocal exit_code
            assert self._process is not None and self._process.stdout is not None
            while True:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip("\n").rstrip("\r")
                if end_marker in line:
                    break
                if "__EXIT_CODE__" in line:
                    parts = line.split("__EXIT_CODE__")
                    if len(parts) > 1:
                        code_str = parts[1].strip()
                        if code_str.lstrip("-").isdigit():
                            exit_code = int(code_str)
                    continue
                stdout_lines.append(line)

        async def _read_stderr():
            assert self._process is not None and self._process.stderr is not None
            while True:
                line_bytes = await self._process.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip("\n").rstrip("\r")
                if stderr_end_marker in line:
                    break
                stderr_lines.append(line)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr()),
                timeout=self.bash_timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            stderr_lines.append(f"Command timed out after {self.bash_timeout}s.")
            exit_code = self.bash_timeout_exit_code
            # timeout 시 session 재시작
            await self._restart_session()

        if exit_code is None:
            exit_code = 0

        stdout_text = "\n".join(stdout_lines)
        stderr_text = "\n".join(stderr_lines)
        output = f"stdout:\n{stdout_text}\n\nstderr:\n{stderr_text}"

        return output, exit_code

    async def execute_bash_command(
        self, command: str, add_to_history: bool = True
    ) -> Tuple[str, int]:
        """AsyncBashExecutor.execute_bash_command()와 동일한 인터페이스.

        Persistent bash session에서 command를 실행한다.
        shell state(환경변수, cd, source 등)가 command 간에 보존된다.
        """
        async with self._command_lock:
            # session이 살아있는지 확인
            if self._process is None or self._process.returncode is not None:
                logging.warning(
                    f"[{self.repository}@{self.revision}] Bash session not running. Restarting."
                )
                await self._restart_session()

            output, exit_code = await self._execute_in_session(command)

        if add_to_history:
            self.commands_history.append({"command": command, "exit_code": exit_code})

        # 출력 길이 제한 (AsyncBashExecutor와 동일 로직)
        if (
            self.max_num_chars_bash_output is not None
            and len(output) > self.max_num_chars_bash_output
        ):
            first_half = output[: self.max_num_chars_bash_output // 2]
            last_half = output[-self.max_num_chars_bash_output // 2 :]
            lines_skipped = output.count(
                "\n", self.max_num_chars_bash_output // 2, -self.max_num_chars_bash_output // 2
            )
            output = f"{first_half}\n\n[... {lines_skipped} lines skipped ...]\n\n{last_half}"

        if exit_code != 0:
            return f"{self.error_message}\n{output}", exit_code

        return output, exit_code

    async def clean(self):
        """AsyncBashExecutor.clean()과 동일한 semantics.

        - Persistent bash session을 종료한다.
        - clear_repo=True이면 다운로드한 repo를 삭제한다.
        - commands_history를 초기화한다.
        """
        try:
            await self._kill_process()
            if self.clear_repo:
                repo_downloader = RepoDownloader(
                    hf_name=self.hf_name, output_dir=self.output_dir, language=self.language
                )
                repo_downloader.clear_repo(repo_name=self.repository, commit_sha=self.revision)
                logging.info(f"[{self.repository}@{self.revision}] Repository removed.")
        except Exception as e:
            logging.error(f"[{self.repository}@{self.revision}] Error cleaning: {e}")
        finally:
            self.commands_history = []
