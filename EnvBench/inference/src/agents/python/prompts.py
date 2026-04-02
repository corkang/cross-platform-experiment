import os
from pathlib import Path
from textwrap import dedent
from typing import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from inference.src.agents.python.state_schema import EnvSetupPythonState

dockerfile_path = Path(__file__).parents[4] / "dockerfiles" / "python.Dockerfile"
dockerfile = dockerfile_path.read_text()

docker_env_description = dedent(
    """\
    You are operating in a Docker container with Python and the necessary system utilities.
    For your reference, the Dockerfile used is:
   ```
    {dockerfile}
   ```
    """
).format(dockerfile=dockerfile)


# GitHub Actions runner 기준으로 실제 보장되는 도구만 preinstalled로 기술.
# 참고: https://github.com/actions/runner-images
native_env_descriptions = {
    "linux": dedent(
        """\
        You are operating on an Ubuntu 22.04 Linux system (GitHub Actions runner).
        Pre-installed tools:
        - Python (with pip) — available via the 'python3' and 'pip3' commands ('python' and 'pip' are also aliased)
        - git, curl, wget
        - build-essential (gcc, g++, make)
        - Node.js, npm

        Tools NOT pre-installed (install them yourself if the project needs them):
        - pyenv — install via: curl https://pyenv.run | bash && export PATH="$HOME/.pyenv/bin:$PATH" && eval "$(pyenv init -)"
        - Poetry — install via: curl -sSL https://install.python-poetry.org | python3 -
        - conda/miniconda, pipenv, uv — install via pip if needed

        You have sudo access. Use 'sudo apt-get update && sudo apt-get install -y <package>' for system packages.
        The script must be non-interactive (use -y flags where needed)."""
    ),
    "macos": dedent(
        """\
        You are operating on a macOS 14 system (Apple Silicon / arm64, GitHub Actions runner).
        Pre-installed tools:
        - Python (with pip) — available via the 'python3' and 'pip3' commands
        - git, curl, wget
        - Xcode Command Line Tools (clang, make)
        - Homebrew (brew)
        - Node.js, npm

        Tools NOT pre-installed (install them yourself if the project needs them):
        - pyenv — install via: brew install pyenv
        - Poetry — install via: curl -sSL https://install.python-poetry.org | python3 -
        - conda/miniconda — install if needed

        You have sudo access. Use 'brew install <package>' for system packages.
        Do NOT use apt-get — this is macOS, not Linux.
        Some Linux package names differ on macOS (e.g., libffi-dev → libffi).
        The script must be non-interactive."""
    ),
    "windows": dedent(
        """\
        You are operating on a Windows Server 2022 system (GitHub Actions runner) with Git Bash as the shell.
        Pre-installed tools:
        - Python (with pip) — available via the 'python' and 'pip' commands
        - git, curl (via Git Bash)
        - Visual Studio Build Tools (cl.exe C/C++ compiler available)
        - Chocolatey (choco) package manager
        - Node.js, npm

        Tools NOT pre-installed (install them yourself if the project needs them):
        - pyenv — use pyenv-win: pip install pyenv-win
        - Poetry — install via: pip install poetry
        - conda/miniconda — install if needed

        You do NOT have sudo. Do NOT use apt-get or brew.
        Use 'pip install <package>' for Python packages.
        Use 'choco install <package> -y' for system tools if needed.
        File paths may use backslash but Git Bash accepts forward slash.
        The script must be non-interactive."""
    ),
}

_VALID_TARGET_OS = frozenset(native_env_descriptions.keys())


def _get_env_description() -> str:
    """EXECUTION_MODE 환경변수에 따라 Docker 또는 Native OS 환경 설명을 반환한다.

    사용법:
      - 원본 Docker 모드:  (기본값, 환경변수 미설정 시)
      - Native 모드:       EXECUTION_MODE=native TARGET_OS=linux|macos|windows
    """
    mode = os.environ.get("EXECUTION_MODE", "docker")
    if mode == "native":
        target_os = os.environ.get("TARGET_OS", "linux")
        if target_os not in _VALID_TARGET_OS:
            raise ValueError(
                f"TARGET_OS='{target_os}' is not supported. "
                f"Valid values: {sorted(_VALID_TARGET_OS)}"
            )
        return native_env_descriptions[target_os]
    else:
        return docker_env_description


def _get_sudo_instruction() -> str:
    """OS에 따라 sudo 사용 가능 여부를 반환한다."""
    mode = os.environ.get("EXECUTION_MODE", "docker")
    if mode == "native":
        target_os = os.environ.get("TARGET_OS", "linux")
        if target_os == "windows":
            return "You are not allowed to use sudo (not available on Windows)."
        else:
            return "You may use sudo if needed for system package installation."
    else:
        return "You are not allowed to use sudo."


def _get_tool_availability_note() -> str:
    """Docker 이미지에는 pyenv, Poetry가 사전 설치되어 있지만,
    Native OS에서는 그렇지 않으므로 프롬프트를 조정한다."""
    mode = os.environ.get("EXECUTION_MODE", "docker")
    if mode == "native":
        return (
            "Note that pyenv, Poetry, and conda may NOT be pre-installed. "
            "Check their availability first (e.g., 'which pyenv') and install them if needed."
        )
    else:
        return (
            "Note that pyenv is available in the environment for Python version management. "
            "Both pip and Poetry are available on the system, you do not need to install them."
        )


def _get_poetry_shell_note() -> str:
    """Docker에서는 poetry shell이 안 되지만, Native에서는 될 수도 있다."""
    mode = os.environ.get("EXECUTION_MODE", "docker")
    if mode == "native":
        return (
            'If using Poetry, run "source $(poetry env info --path)/bin/activate" '
            "to activate the environment."
        )
    else:
        return (
            'Don\'t run "poetry shell" as it does not work in the Docker container. '
            'Instead, run "source $(poetry env info --path)/bin/activate".'
        )


_SYSTEM_PROMPT_TEMPLATE = dedent(
    """\
    You are an intelligent AI agent with the goal of installing and configuring all necessary dependencies for a given Python repository.
    The repository is already located in your working directory, so you can immediately access all files and folders.

    Several points to keep in mind:
    1. Always start by examining the repository structure (e.g., root folder, subfolders like 'src', 'libs', or similarly named folders)
       to locate potential dependency definitions such as requirements.txt, pyproject.toml, setup.py, setup.cfg, or alternative files.
    2. Check for references to Python versions or specialized environment requirements (e.g., in a readme, documentation, or
       advanced installation instructions). {tool_availability}
    3. Ensure that you install or switch to the correct Python version if it is specified, or use the latest available Python
       if no specific version is mentioned. To install a Python version via pyenv, e.g., 3.10.10, run `pyenv install 3.10.10`.
       To configure the system to use a specific version, e.g., 3.10.10, run `pyenv global 3.10.10`.
       Always check already available Python versions with `pyenv versions` before running `pyenv install` or use `pyenv install -f`
       to avoid the command hanging indefinitely if attempting to install already available version.
    4. Identify the dependency manager used in this repository (pip or Poetry).
    5. Follow any additional build or installation instructions discovered in the repository (e.g., system-level dependencies,
       custom compilation steps, or environment variable configuration).
    6. Use the identified dependency manager or the best possible approach (pip install, poetry install, etc.)
       to install the project's dependencies. Pay attention to potential dev dependencies or additional steps that might be needed
       (e.g., migrations, data downloads, or plugin installation).
    7. Take note that if you do not call a tool in your response, the system will interpret this as a signal that you consider the job done.
       Therefore, continue to use the provided Bash terminal tool for all intermediate steps until you are completely finished.
    8. When you have finished installing the dependencies and the project is ready, ensure that the Python project can be run
       simply by using the "python" command (which should point to the correct executable). {poetry_shell_note}
    9. After validating that everything works (importing packages, additional script or build steps, etc.), provide your final summary
       message without any further tool calls.
    10. If you are building or installing the current repository itself (e.g., "project A"), do not install it as a PyPI package
        (such as running "pip install A --user" or "pip install projectA" directly). Instead, make sure you install and use the
        project code from the specific revision provided in the repository (for example, via "pip install -e ." or another local
        requirement approach). Even if the readme instructions mention a standard pip command for the published PyPI package,
        remember that your task is to install from the local repository, not from PyPI.

    A short example of how you might investigate and carry out installation steps (this is purely illustrative):
    - You would use 'ls' to explore files and folders in the working directory.
    - You would notice there is an INSTALL.md file, so you run 'cat INSTALL.md' to read the installation instructions.
    - Those instructions might say, for example, "Run pip install -e .[ci] to install local packages and install additional system-level packages
      for PyLaTeX via apt-get: texlive-pictures texlive-science texlive-latex-extra latexmk".
    - You would install system packages, verify that it completed successfully, and do further steps to fix any issues if necessary.
    - You would install local packages with `pip install -e .[ci]`, verify that it completed successfully, and do further steps to fix any issues if necessary.

    {env_description}

    Remember:
    - You must execute all intermediate steps (installing packages, copying files, etc.) via the provided Bash terminal,
      within the repository root.
    - Only provide your concluding response without a tool call once you are confident the job is finished
      (all dependencies installed, Python environment properly configured, and the repository ready to run).
    - Mind the commands that might require additional interactive confirmation, as you would not be able to give it.
      Always try to invoke commands in a way that won't get them hanging, e.g., `apt-get install -y` instead of `apt-get install`.
    - {sudo_instruction}
    """
)


def get_system_prompt() -> str:
    """실행 시점에 환경변수를 읽어 system prompt를 조립한다.

    import 시점이 아니라 호출 시점에 환경변수를 읽으므로,
    workflow에서 EXECUTION_MODE/TARGET_OS를 프로세스 시작 전에 설정하면 정확히 반영된다.
    """
    return _SYSTEM_PROMPT_TEMPLATE.format(
        env_description=_get_env_description(),
        sudo_instruction=_get_sudo_instruction(),
        tool_availability=_get_tool_availability_note(),
        poetry_shell_note=_get_poetry_shell_note(),
    )


# 하위 호환: 기존 코드에서 system_prompt를 직접 참조하는 경우를 위해 유지.
# 단, import 시점에 고정되므로 가능하면 get_system_prompt()를 사용하는 것이 바람직하다.
system_prompt = get_system_prompt()


def get_env_setup_python_prompt(state: EnvSetupPythonState) -> Sequence[BaseMessage]:
    existing_messages = state.get("messages", [])
    if not isinstance(existing_messages, list):
        existing_messages = list(existing_messages)

    user_prompt = []

    if "build_instructions" in state and state["build_instructions"]:
        user_prompt.append(
            dedent(f"""
        There are installation instructions for the current project that might be helpful to complete your task:

        ```
        {state["build_instructions"]}
        ```
        """)
        )
    else:
        user_prompt.append(
            dedent("""
            There are no installation instructions for the current project,
            so make sure to explore the contents of the repositority thoroughly via provided tools.""")
        )
    return [SystemMessage(content=get_system_prompt()), HumanMessage(content="\n".join(user_prompt))] + existing_messages
