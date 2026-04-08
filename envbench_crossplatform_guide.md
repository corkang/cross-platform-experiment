# EnvBench Cross-Platform 실험 가이드

GitHub Actions runner 기반 native OS에서 EnvBench `bash agent`를 직접 실행하고, EnvBench의 기존 Python evaluation metric으로 성능을 측정하기 위한 설계 가이드다.

이 문서의 목적은 구현 코드를 길게 제시하는 것이 아니라, 각 단계에서 무엇을 바꾸고 왜 바꾸는지, 어떤 파일이 수정 대상인지, 어떤 요구사항과 제약을 지켜야 하는지, 무엇으로 검증할지를 명확히 정리하는 것이다. 이후 단계별로 Codex나 다른 에이전트에 작업을 맡길 때 이 문서를 그대로 작업 명세로 사용할 수 있어야 한다.

## 0. 실험 목표와 전제

### 연구 질문

- Linux/Docker 전제로 설계된 EnvBench `bash agent`가 native `ubuntu-22.04`, `macos-14`, `windows-2022` 환경에서도 development environment setup을 수행할 수 있는가?
- OS가 바뀌었을 때 setup 실패는 어떤 유형으로 나타나는가?

### 이번 파일럿의 범위

- 대상 언어: Python
- 대상 OS: `ubuntu-22.04`, `macos-14`, `windows-2022`
- 대상 repo: `manifests/selected_repos.jsonl`에 있는 10개 repo
- 평가 방식: EnvBench의 기존 Python metric 유지
  - `bootstrap script` 실행
  - `pyright` 실행
  - `reportMissingImports` 개수 집계
  - `pass@1 = (exit_code == 0) and (issues_count == 0)`

### 이 문서가 다루지 않는 것

- 단순 `pip install` 기반 heuristic setup workflow 작성
- Linux에서 생성한 script를 다른 OS에서 재실행하는 portability-only 실험
- 전체 Python split 329개 repo에 대한 full-scale 실험

### 먼저 고정해야 할 사실

- 현재 root workflow [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml)은 EnvBench `bash agent` 실험이 아니라 heuristic setup 실험이다.
- 이 문서는 그 workflow를 직접 조금 수정하는 문서가 아니라, EnvBench 내부 `inference`와 `evaluation`를 native runner 기준으로 재구성하는 문서다.
- `macos-14`는 Apple Silicon `arm64`이므로, Linux/Windows와 비교할 때 OS 차이와 architecture 차이가 일부 함께 들어간다.

## 1. 원본 EnvBench 구조와 수정 방향 정리

### 목표

- Docker 기반 원본 파이프라인과 native runner 기반 확장 파이프라인의 차이를 명확히 문서화한다.
- 이후 수정이 필요한 코드 경로를 확정한다.

### 원본 파이프라인

```text
Inference:
  EnvBench agent -> Docker container 안에서 bash tool 실행 -> trajectory 생성

Processing:
  trajectory -> scripts.jsonl

Evaluation:
  Docker container에서 bootstrap_script.sh 실행 -> pyright metric 수집 -> results.jsonl
```

### target 파이프라인

```text
Inference:
  EnvBench bash agent -> GitHub Actions native OS에서 bash tool 실행 -> trajectory 생성

Processing:
  trajectory -> scripts.jsonl

Evaluation:
  같은 native OS에서 bootstrap_script.sh 실행 -> pyright metric 수집 -> results.jsonl
```

### 핵심 차이

- Docker container를 없애고 runner OS 자체를 execution environment로 사용한다.
- metric 정의는 바꾸지 않고 execution substrate만 바꾼다.
- inference와 evaluation 모두 native OS 전환이 필요하다.

### 이 단계에서 확인할 파일

- [EnvBench/inference/src/agents/python/prompts.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/agents/python/prompts.py)
- [EnvBench/inference/configs/toolkit_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/toolkit_config.py)
- [EnvBench/inference/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/main.py)
- [EnvBench/evaluation/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/main.py)
- [EnvBench/evaluation/scripts/python_build.sh](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/scripts/python_build.sh)

### 이 단계의 요구사항

- 어떤 부분이 Docker 자체에 의존하는지와, 어떤 부분이 단순 config reuse인지 구분해야 한다.
- “Docker 제거”와 “metric 변경”을 혼동하지 않아야 한다.
- Windows도 가능하면 orchestration shell은 `bash` 기준으로 가져간다.

### 산출물

- 수정 대상 파일 목록
- 각 파일의 책임과 수정 목적 요약
- 원본 대비 변경 불가 항목 목록
  - Python metric 정의
  - `pass@1` 정의
  - trajectory -> scripts -> results 흐름

### 검증 기준

- 문서만 읽고도 “어떤 파일을 왜 수정하는지”를 설명할 수 있어야 한다.
- 현재 heuristic workflow와 본 실험 workflow가 다르다는 점이 분명해야 한다.

### 현재 코드 상태 점검

아래 내용은 현재 레포 상태를 직접 확인한 결과다. 섹션 3, 4, 5의 구현 작업은 이 상태 점검을 기준으로 이어서 진행한다.

#### A. 현재 root workflow는 EnvBench `bash agent` 실험이 아니다

확인 파일:
- [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml)

현재 상태:
- `uv sync`로 EnvBench 의존성은 설치하지만, 실제 실험 단계는 EnvBench `inference -> processing -> evaluation`를 호출하지 않는다.
- 대신 `scripts/run_setup_linux.sh`, `scripts/run_setup_macos.sh`, `scripts/run_setup_windows.ps1`를 호출해 `venv + pip install` 수준의 heuristic setup만 수행한다.
- 평가 단계는 `run_eval_linux.sh`, `run_eval_macos.sh`, `run_eval_windows.ps1`를 호출하도록 되어 있는데, 해당 파일들은 현재 레포에 존재하지 않는다.
- matrix도 현재는 `ubuntu-latest`만 활성화되어 있고, repo 목록도 일부만 켜져 있다.

판단:
- 이 workflow는 “cross-platform heuristic setup smoke test”에는 가깝지만, EnvBench `bash agent` 성능 측정 workflow로는 사용할 수 없다.
- 문서에서 이 workflow는 참고용 기존 시도로만 언급하고, 본 실험은 별도 workflow로 설계한다고 명시하는 편이 맞다.

수정 방향:
- 섹션 5에서는 이 파일을 직접 확장하기보다 새 workflow를 만들 것을 기본 방향으로 둔다.
- 새 workflow는 EnvBench 내부 파이프라인을 직접 호출해야 한다.

#### B. `prompts.py`는 이미 native 분기 초안이 들어가 있다

확인 파일:
- [EnvBench/inference/src/agents/python/prompts.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/agents/python/prompts.py)

현재 상태:
- Docker 설명과 native OS 설명을 `EXECUTION_MODE` / `TARGET_OS` 환경변수로 분기하는 초안이 이미 들어가 있다.
- `linux`, `macos`, `windows`별 설명도 들어가 있으며, 적어도 다음 핵심 제약은 반영되어 있다.
  - Linux에서는 `apt-get`
  - macOS에서는 `brew`
  - Windows에서는 `sudo` 금지, `apt-get`/`brew` 금지
- “repo가 이미 working directory에 있다”, “non-interactive command를 써야 한다” 같은 핵심 instruction도 유지되고 있다.

잘된 점:
- Docker-only prompt에서 native-aware prompt로 가려는 방향 자체는 맞다.
- 기존 system prompt 구조를 크게 깨지 않고 OS별 분기를 추가했다.

남은 문제:
- prompt가 가정하는 preinstalled tool 목록이 아직 GitHub Actions workflow에서 실제로 보장되는 도구 목록과 정확히 맞지 않는다.
- Windows 설명은 `Git Bash`, `Chocolatey`, `Visual Studio Build Tools`를 가정하지만, 실제 workflow에서 무엇을 명시적으로 설치하고 무엇을 runner 기본 제공으로 간주할지 아직 고정되지 않았다.
- `system_prompt`가 import 시점에 environment variable을 읽어 고정되므로, workflow에서 `EXECUTION_MODE`와 `TARGET_OS`를 프로세스 시작 전에 정확히 주입해야 한다.

수정 방향:
- 섹션 3 구현 시 prompt의 방향은 유지하되, “실제 workflow에서 보장하는 도구만 preinstalled”라는 원칙으로 설명을 더 엄밀하게 줄여야 한다.
- OS별 금지 명령과 package manager 제약은 유지한다.

#### C. `toolkit_config.py`는 native 분기 로직이 들어갔지만 현재 import가 깨져 있다

확인 파일:
- [EnvBench/inference/configs/toolkit_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/toolkit_config.py)

현재 상태:
- `EXECUTION_MODE == "native"`일 때 `NativeShellExecutor`를 사용하도록 분기 로직이 이미 들어가 있다.
- Docker mode에서는 기존 `AsyncBashExecutor`를 그대로 사용한다.

잘된 점:
- backend 선택 지점을 `toolkit_config.py`에 둔 것은 적절하다.
- 기존 toolkit 인터페이스를 유지한 채 native executor를 끼워 넣는 방향도 맞다.

남은 문제:
- import 경로가 `from inference.src.native_shell_executor import NativeShellExecutor`로 되어 있는데, 실제 파일은 `EnvBench/inference/native_shell_executer.py`에 있다.
- 즉, 현재 상태로는 native mode에서 import 자체가 실패할 가능성이 높다.

수정 방향:
- 섹션 3 구현 시 native executor 파일 위치와 import path를 하나로 정리해야 한다.
- 가능하면 `src` 아래로 옮기거나, 반대로 import를 실제 위치에 맞추되 파일명 오타까지 같이 바로잡는 것이 필요하다.

#### D. native executor는 초안이 있지만 “bash 보장”과 cleanup semantics가 아직 불완전하다

확인 파일:
- [EnvBench/inference/native_shell_executer.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/native_shell_executer.py)
- [EnvBench/inference/src/async_bash_executor.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/async_bash_executor.py)

현재 상태:
- `NativeShellExecutor` 초안이 존재한다.
- repo download, working directory 설정, timeout, command history 기록, output truncation 등 기본 골격은 이미 들어가 있다.

잘된 점:
- `AsyncBashExecutor`와 유사한 생성 시그니처를 맞추려는 시도가 있다.
- downstream toolkit이 기대하는 `execute_bash_command()` / `clean()` 인터페이스도 맞추고 있다.

남은 문제:
- 파일명 자체가 `executer`로 되어 있고, import 쪽은 `executor`를 기대하고 있어 이름 정리가 필요하다.
- `asyncio.create_subprocess_shell()`은 OS 기본 shell을 사용하므로, 이름과 달리 “bash command 실행”을 보장하지 않는다.
  - Linux/macOS에서는 `/bin/sh`일 수 있다.
  - Windows에서는 Git Bash가 아니라 다른 shell로 갈 가능성이 있다.
- 기존 `AsyncBashExecutor.clean()`은 `clear_repo` 설정에 따라 repo cleanup까지 수행하는데, 현재 native executor의 `clean()`은 command history만 비우고 repo cleanup을 하지 않는다.
- 사용하지 않는 import가 남아 있고, 현재 구현은 “초안” 단계에 가깝다.

수정 방향:
- 섹션 3 구현 시 native executor는 “명시적으로 bash를 실행하는 방식”으로 바꿔야 한다.
- `clear_repo` semantics를 기존 executor와 맞춰야 한다.
- 파일명, import 경로, cleanup 동작을 `AsyncBashExecutor`와 최대한 일치시키는 방향으로 정리한다.

#### E. `inference/main.py`는 native mode를 직접 모르지만, 현재 구조상 큰 진입점 문제는 없다

확인 파일:
- [EnvBench/inference/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/main.py)

현재 상태:
- `process_single_datapoint()`는 여전히 `config.docker.*` 값을 toolkit instantiation에 넘긴다.
- 하지만 실제 backend 선택은 `toolkit_config.py`에서 처리하므로, native mode에서도 이 값을 재사용하는 구조 자체는 가능하다.

잘된 점:
- inference main을 크게 뜯지 않고도 native mode를 수용할 가능성이 있다.
- trajectory 업로드, config 업로드, commit hash 업로드 등 기존 산출물 흐름은 유지된다.

남은 문제:
- logging 문구와 cleanup 경고가 여전히 container 기준 표현을 쓰고 있다.
- `docker` config block은 native mode에서도 참조되므로, 섹션 3 문서에서는 “docker 블록 삭제”가 아니라 “일부 execution parameter 재사용”으로 설명해야 한다.

수정 방향:
- 섹션 3 구현 시 `inference/main.py`는 대규모 변경 대상이라기보다, native mode에서도 오해 없이 동작하도록 logging과 dependency 설명을 정리하는 보조 수정 대상으로 적는 것이 맞다.

#### F. `evaluation/main.py`는 native path 초안이 이미 들어가 있지만 cross-platform 완성도는 아직 낮다

확인 파일:
- [EnvBench/evaluation/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/main.py)

현재 상태:
- `run_opensource()`는 여전히 Docker 기반 평가 경로다.
- 별도로 `run_native()`가 추가되어 있고, `eval_tools = {"opensource": ..., "native": ...}`로 선택 가능하다.

잘된 점:
- native evaluation path를 별도 함수로 분리한 방향은 맞다.
- repo download -> bootstrap 작성 -> build script 작성 -> 실행 -> `results.json` 수집 -> JSON 저장의 기본 흐름도 이미 들어가 있다.

남은 문제:
- `run_native()`는 `sp.run(["bash", build_path], ...)`로 실행하는데, Windows runner에서 이 방식이 그대로 동작한다고 보장할 수 없다.
- timeout은 여전히 `cfg.docker.container_timeout`을 재사용하고 있어 문서에서 설명을 명확히 해야 한다.
- field 이름이 `container_logs`로 남아 있어 native 실행 의미와 어긋난다. 다만 결과 스키마 호환성을 위해 유지할지 바꿀지는 명시적 결정이 필요하다.
- `platform` import가 들어가 있지만 현재 native path에서는 활용되지 않는다.

수정 방향:
- 섹션 4 구현 시 `run_native()`는 “공식 경로로 승격할 후보”라고 적고, bash invocation 방식과 timeout semantics를 정리 대상으로 명시한다.
- 결과 스키마는 가능하면 유지하고, 필드 의미 설명을 문서에 추가한다.

#### G. `python_build.sh`는 metric 방향은 맞지만 jq 의존 때문에 cross-platform blocker다

확인 파일:
- [EnvBench/evaluation/scripts/python_build.sh](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/scripts/python_build.sh)

현재 상태:
- bootstrap script를 실행하고, `python -m pyright . --level error --outputjson`를 돌린 뒤 `reportMissingImports` 개수를 집계한다.
- 전체적인 metric 방향은 원본 EnvBench와 일치한다.

잘된 점:
- `reportMissingImports`만 세는 핵심 metric은 유지되고 있다.
- repo root에서 pyright를 실행하는 흐름도 맞다.

남은 문제:
- `jq`가 없으면 설치를 시도하는데, 이 로직은 Linux/macOS/Windows 공통 실행 관점에서 불안정하다.
- `apt-get`, `brew`, `pip install jq` fallback은 runner나 shell에 따라 실패하거나 불필요한 side effect를 만들 수 있다.
- cross-platform 평가를 위해서는 JSON 집계를 Python으로 대체하는 편이 더 안전하다.
- `chmod -R 777 .` 같은 Docker 중심 습관은 native runner에서는 과할 수 있다.

수정 방향:
- 섹션 4 구현 시 이 파일은 “metric은 유지하고 구현만 cross-platform하게 다시 정리해야 하는 파일”로 명시한다.
- 특히 `jq` 제거가 핵심 변경 포인트임을 적는다.

#### H. 섹션 1 기준 결론

현재 레포는 “native cross-platform 실험을 향해 부분 수정이 이미 시작된 상태”다. 완전히 처음부터 시작하는 것은 아니지만, 그대로는 실행이 안 되거나 해석이 어긋나는 지점이 분명히 있다.

현재 수정 상태를 한 줄로 요약하면 다음과 같다.

- prompt: 방향은 맞지만 workflow와 toolchain 가정 정합화 필요
- toolkit config: 분기 방향은 맞지만 import path broken
- native executor: 초안 존재, 그러나 bash 보장과 cleanup semantics 부족
- inference main: 큰 구조는 재사용 가능, 설명과 logging 정리 필요
- evaluation main: native path 초안 존재, cross-platform 실행 방식 정리 필요
- python build script: metric 유지, 구현만 cross-platform하게 재작성 필요
- root workflow: EnvBench agent 실험용으로는 부적절, 새 workflow 필요

## 2. 대상 데이터와 manifest 확정

### 목표

- 파일럿 대상 repo와 commit을 고정한다.
- workflow와 evaluation이 참조할 manifest 형식을 고정한다.

### 기준 파일

- [manifests/selected_repos.jsonl](/Users/corkang/Desktop/Research/EnvBench-Multi/manifests/selected_repos.jsonl)
- [manifests/python10.json](/Users/corkang/Desktop/Research/EnvBench-Multi/manifests/python10.json)

### 사용 규칙

- 실험 대상 repo는 `selected_repos.jsonl`을 single source of truth로 사용한다.
- manifest schema는 아래 두 필드를 유지한다.
  - `repository`
  - `revision`
- workflow matrix는 이 파일의 정보를 기준으로 생성한다.

### repo 이름 교정 규칙

- `skrub-data/skrub`가 맞다. `krub-data/skrub`는 오타다.
- `robbievanleeuwen/section-properties`가 맞다. `section-propertiess`는 오타다.

### 이 단계의 요구사항

- repo 목록과 pinned commit은 문서에 명시하되, 값의 source는 항상 manifest라고 적는다.
- 문서 안에 repo 목록을 하드코딩하더라도 manifest와 불일치하면 manifest를 우선한다고 적는다.
- workflow나 helper script가 별도 포맷을 요구하더라도 최종 source manifest는 바꾸지 않는다.

### 산출물

- 10개 repo + commit이 확정된 manifest
- workflow에서 matrix로 변환하는 입력 데이터 규칙

### 검증 기준

- 10개 repo가 모두 manifest에 존재해야 한다.
- 이후 workflow가 `repository` / `revision` 스키마를 그대로 소비할 수 있어야 한다.

### 현재 코드 상태 점검

이 섹션은 [selected_repos.jsonl](/Users/corkang/Desktop/Research/EnvBench-Multi/manifests/selected_repos.jsonl)을 기준으로 고정한다. 현재 레포 상태를 기준으로 보면, manifest 관련 판단은 다음처럼 정리된다.

#### A. `selected_repos.jsonl`를 single source of truth로 쓰는 것이 맞다

확인 파일:
- [selected_repos.jsonl](/Users/corkang/Desktop/Research/EnvBench-Multi/manifests/selected_repos.jsonl)

현재 상태:
- 10개 repo가 모두 들어 있다.
- 각 row는 `repository`, `revision` 두 필드를 가진 JSONL 형식이다.
- 각 repo에 pinned commit SHA가 이미 고정되어 있다.

잘된 점:
- 이 형식은 EnvBench 내부 코드와 자연스럽게 맞는다.
- inference와 evaluation 주변 코드가 이미 `repository` / `revision` naming을 사용한다.

근거:
- [EnvBench/conf/base.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/conf/base.yaml)에서 evaluation input column mapping이 `repo_name: repository`, `commit_sha: revision`으로 되어 있다.
- [EnvBench/env_setup_utils/process_trajectories_to_scripts.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/env_setup_utils/process_trajectories_to_scripts.py)는 output script row를 `repository`, `revision`, `script`로 저장한다.
- [EnvBench/inference/src/env_setup_runner.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/env_setup_runner.py)는 trajectory 파일명을 `<repository>@<revision>.jsonl` 규칙으로 만든다.

판단:
- 파일럿 실험의 canonical manifest는 `selected_repos.jsonl`로 확정하는 것이 맞다.
- 이후 workflow matrix, single-repo smoke manifest, 결과 집계 기준 모두 이 파일에서 파생되도록 설계해야 한다.

#### B. `python10.json`은 보조 파일일 뿐, 현재 상태로는 source of truth로 쓰면 안 된다

확인 파일:
- [python10.json](/Users/corkang/Desktop/Research/EnvBench-Multi/manifests/python10.json)

현재 상태:
- repo 이름 목록만 있고 commit SHA가 없다.
- `krub-data/skrub` 오타가 남아 있다.
- `selected_repos.jsonl`과 달리 evaluation/inference가 바로 읽을 수 있는 스키마도 아니다.

판단:
- `python10.json`은 “10개 후보 repo 이름 목록을 사람 눈으로 보는 참고 파일” 정도로만 취급해야 한다.
- workflow나 evaluation 입력 source로 직접 쓰면 안 된다.

수정 방향:
- 문서에는 `python10.json`을 참고용 목록 파일로만 언급한다.
- 이후 정리 단계에서 오타를 맞추거나, 역할이 중복되면 삭제 여부를 검토한다.
- 하지만 지금 당장 중요한 것은 `selected_repos.jsonl`를 기준으로 나머지 설계를 고정하는 것이다.

#### C. manifest schema는 바꾸지 않는 것이 맞다

현재 상태:
- 레포 내부 여러 컴포넌트가 이미 `repository` / `revision` naming을 기대하거나 쉽게 매핑 가능하다.
- evaluation config도 이 naming을 그대로 받아들일 수 있다.

판단:
- 이번 파일럿에서는 schema를 `repo_name` / `commit_sha` 같은 새 이름으로 바꾸지 않는다.
- 외부 workflow matrix나 helper script가 다른 naming을 선호하더라도, 변환은 workflow 내부의 임시 단계에서만 하고 저장 포맷은 유지한다.

권장 규칙:
- canonical manifest: `repository`, `revision`
- workflow 내부 변수명: 필요하면 `REPOSITORY`, `REVISION` 또는 `repo_name`, `commit_sha`로 일시 변환 가능
- HF에 올라가는 trajectories/scripts/results와 연결되는 데이터 row는 가능하면 canonical naming을 유지

#### D. 섹션 2에서 확정해야 하는 downstream contract

이 섹션이 끝나면 아래 계약을 문서에서 고정해야 한다.

- 입력 manifest source는 `manifests/selected_repos.jsonl`
- 각 row는 정확히 아래 스키마를 가진다.
  - `repository: str`
  - `revision: str`
- workflow matrix는 이 파일에서 생성한다.
- 1-repo smoke test를 할 때도 이 파일에서 한 줄만 뽑아 임시 JSONL을 만든다.
- 결과 파일 naming의 repo 식별자는 항상 `repository.replace("/", "__")` 규칙을 따른다.
- trajectory, script, evaluation result를 연결할 때 repo identity는 `repository + revision` 쌍으로 정의한다.

#### E. 현재 10개 repo와 pinned revision

섹션 2의 문서에는 아래 값이 이미 확정된 것으로 적는다.

1. `pytest-dev/pytest-xdist` @ `c7b4f6114479d4053f0ef8411ed5e0a410b207f4`
2. `facebookresearch/hydra` @ `2e682d84e789d82dd11ab1f329f2dd1966fa6b54`
3. `jageo/lobsterpy` @ `55d8d2e119aa1147166994d57fbdbe10931cc748`
4. `mad-lab-fau/biopsykit` @ `2ad99fba5f55328109c72bd3cbb72ba2444da078`
5. `skrub-data/skrub` @ `0d69d97129aa638167e1e721686acb066daed007`
6. `vacanza/python-holidays` @ `472e89d889cf3a5c5301d54bc642f6c149f55ee5`
7. `mov-cli/mov-cli` @ `32f8af4f15804dd6ce88d8f781a12b918119e64c`
8. `cookiecutter/cookiecutter` @ `f49dcf975ef0390cd19c8f246a42638a1c8e8a42`
9. `robbievanleeuwen/section-properties` @ `0a41ea141394ccca9df1625e1d44ea132a2ccf37`
10. `dagshub/client` @ `f8d89c53c733e58edf36f409f7a27cd30d80eced`

문서상 주의사항:
- 이 목록은 설명용으로 적되, 최종 truth는 언제나 `selected_repos.jsonl`이다.
- 값이 바뀌면 문서보다 manifest를 먼저 업데이트해야 한다.

#### F. 섹션 2 기준 결론

현재 코드와 문서 상태를 종합하면, 섹션 2에서 추가로 의사결정할 것은 거의 없다. 이미 필요한 선택은 끝나 있다.

한 줄 결론:
- `selected_repos.jsonl`를 파일럿 실험의 canonical manifest로 확정한다.
- `repository` / `revision` 스키마를 유지한다.
- `python10.json`은 보조 목록 파일로만 취급한다.
- 이후 섹션 4, 5, 실제 workflow 구현은 전부 이 계약 위에서 진행한다.

## 3. Inference를 native OS에서 실행하도록 수정

### 목표

- EnvBench `bash agent`가 Docker container가 아니라 GitHub Actions runner의 native OS에서 명령을 실행하도록 바꾼다.
- 기존 agent 인터페이스와 trajectory 산출물 형식은 유지한다.

### 수정 대상 파일

- [EnvBench/inference/src/agents/python/prompts.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/agents/python/prompts.py)
- [EnvBench/inference/configs/toolkit_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/toolkit_config.py)
- `native shell executor` 구현 파일
- [EnvBench/inference/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/main.py)
- [EnvBench/inference/configs/run_inference_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/run_inference_config.py)
- [EnvBench/inference/configs/docker_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/docker_config.py)
- [EnvBench/inference/src/toolkits/base.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/toolkits/base.py)
- [EnvBench/inference/src/toolkits/bash_terminal.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/toolkits/bash_terminal.py)

### 실제 변경 순서

섹션 1의 점검 결과를 기준으로, inference 쪽은 아래 순서로 수정하는 것이 안전하다.

1. native executor 파일 위치와 이름을 먼저 정리한다.
2. `toolkit_config.py`에서 executor 분기를 정상화한다.
3. toolkit base type과 설명 문구를 executor-agnostic하게 바꾼다.
4. `prompts.py`에서 workflow와 맞는 native OS 설명을 정밀화한다.
5. `inference/main.py`와 config 설명을 native mode 기준으로 보강한다.
6. 마지막에 1-repo smoke test로 trajectory가 남는지 확인한다.

이 순서를 바꾸면, prompt만 먼저 바뀌고 실제 native backend가 import에서 깨지는 식의 반쪽 상태가 남을 수 있다.

### 파일별 변경 방향

#### A0. 우선 정리해야 할 구조적 불일치

현재 섹션 1에서 이미 확인된 blocking issue:

- `toolkit_config.py`는 `inference.src.native_shell_executor`를 import하지만 실제 파일은 `EnvBench/inference/native_shell_executer.py`에 있다.
- `NativeShellExecutor`는 이름상 bash executor인데 실제로는 OS 기본 shell에 의존한다.
- `BaseEnvSetupToolkit`는 `AsyncBashExecutor`만 타입으로 가정하고 설명도 Docker container 기준이다.

이 세 가지는 서로 연결되어 있으므로 한 번에 정리해야 한다.

#### A. `prompts.py`

역할:
- agent가 어떤 환경에서 동작하는지 설명하는 system prompt를 제공한다.

변경 요구사항:
- Docker 설명과 native runner 설명을 분기할 수 있어야 한다.
- `linux`, `macos`, `windows`별로 다음을 명확히 적어야 한다.
  - 허용되는 패키지 관리자
  - 금지되는 명령
  - sudo 사용 가능 여부
  - non-interactive requirement
  - repo가 이미 working directory에 존재한다는 사실
- prompt는 실제 workflow가 보장하는 도구만 preinstalled라고 가정해야 한다.
- workflow에서 보장하지 않는 도구를 “기본 제공”으로 쓰면 안 된다.

실제 코드 변경 기준:
- 현재 있는 `native_env_descriptions` 분기 구조는 유지해도 된다.
- 대신 각 OS 설명을 “GitHub Actions runner에서 정말 보장되는 도구” 기준으로 다시 써야 한다.
- `pyenv`, `Poetry`, `conda`는 preinstalled가 아니라 “필요시 agent가 설치해야 하는 후보”로 남겨야 한다.
- `TARGET_OS`가 잘못 들어왔을 때 바로 KeyError가 나지 않도록 fallback 또는 validation을 추가하는 것이 바람직하다.
- 현재 `system_prompt`는 import 시점에 environment variable을 읽어 문자열을 고정하므로, 실행 시점 반영이 필요하면 함수 호출 시 prompt를 조립하는 구조로 바꾸는 것도 고려한다.

필수 constraints:
- Linux: `apt-get` 가능
- macOS: `brew` 가능, `apt-get` 금지
- Windows: `sudo` 금지, `apt-get`/`brew` 금지, 가능하면 `choco` 또는 `pip`

추가 constraints:
- “이미 repo root에 있다”는 문구는 반드시 유지한다.
- “non-interactive” requirement는 모든 OS 설명에 공통으로 유지한다.
- Windows 설명은 Git Bash 기준이라는 점을 분명히 적되, path separator와 shell 차이를 과장하지 않는다.

완료 조건:
- Docker mode와 native mode 모두 prompt 생성이 깨지지 않는다.
- `EXECUTION_MODE=native`, `TARGET_OS=linux|macos|windows` 조합에서 서로 다른 prompt가 정상 생성된다.

#### B. `toolkit_config.py`

역할:
- agent가 사용할 bash execution backend를 선택한다.

변경 요구사항:
- Docker executor와 native executor를 환경변수 또는 config로 분기해야 한다.
- native mode에서 import path가 실제 파일 경로와 일치해야 한다.
- 기존 toolkit 타입(`bash`, `bash_python`, `bash_jvm`, `installamatic`) 인터페이스를 깨지 않아야 한다.

실제 코드 변경 기준:
- 먼저 native executor 파일을 import 가능한 위치로 옮기거나 import 구문을 실제 위치에 맞춘다.
- executor 선택 기준은 현재처럼 `EXECUTION_MODE` 환경변수 기반으로 유지해도 된다.
- 단, native 분기와 Docker 분기 모두 동일한 생성 시그니처를 받는다는 사실을 문서화하고 유지해야 한다.
- 이 파일은 “분기만 담당”해야 하며, OS별 세부 실행 로직을 여기로 끌어오면 안 된다.

필수 constraints:
- native mode 선택 방식은 workflow에서 주입할 수 있어야 한다.
- 기존 Docker mode는 깨지지 않게 유지하는 것이 바람직하다.

완료 조건:
- native mode에서 import error 없이 executor가 instantiate된다.
- Docker mode regression 없이 기존 코드 경로도 그대로 작동한다.

#### C. native shell executor

역할:
- Docker container 대신 native OS에서 bash command를 실행한다.

변경 요구사항:
- 파일명과 import path를 일치시켜야 한다.
- `AsyncBashExecutor`와 최대한 같은 인터페이스를 유지해야 한다.
- 아래 behavior를 유지해야 한다.
  - repo download
  - working directory 설정
  - timeout 처리
  - command history 저장
  - output truncation
  - clean up 훅

실제 코드 변경 기준:
- 파일명을 `native_shell_executor.py`로 정리하고, import 경로도 여기에 맞춘다.
- 구현 위치는 가능하면 `EnvBench/inference/src/` 아래로 옮겨서 다른 executor와 계층을 맞추는 편이 낫다.
- `execute_bash_command()`는 이름 그대로 실제 `bash`를 실행해야 한다.
  - OS 기본 shell에 맡기지 말고, workflow가 보장하는 bash 실행 파일을 명시적으로 사용해야 한다.
  - Windows에서도 Git Bash를 쓰는 전제를 workflow와 맞춘다.
- `create()`는 현재처럼 `AsyncBashExecutor.create()`와 유사한 시그니처를 유지한다.
- `clean()`은 기존 Docker executor처럼 `clear_repo` 설정에 따라 repo 삭제까지 수행해야 한다.
- output formatting은 현재처럼 `stdout:\n...\n\nstderr:\n...` 형태를 유지한다.
- timeout 시 exit code와 error message 형식도 기존과 최대한 맞춘다.

필수 constraints:
- 반환값 형식은 기존 toolkit이 그대로 사용할 수 있어야 한다.
- 에러 메시지 형식과 timeout exit code도 가능하면 기존과 맞춘다.

추가 constraints:
- `commands_history`는 processing 단계가 그대로 읽을 수 있어야 한다.
- repo cleanup semantics는 Docker executor와 달라지면 안 된다.
- shell invocation 방식은 Linux/macOS/Windows에서 최대한 동일해야 한다.

완료 조건:
- native executor 단독 smoke test에서 `pwd`, `ls`, `python --version` 같은 명령이 정상 실행된다.
- `clean()` 호출 후 `clear_repo=true`일 때 repo가 정리된다.

#### D. `inference/main.py`

역할:
- 전체 inference 파이프라인을 실행한다.

변경 요구사항:
- native mode에서도 현재 pipeline 흐름이 그대로 유지되어야 한다.
- `docker` config block을 완전히 없애지 말고, timeout/output 설정 등 재사용 여부를 명확히 한다.
- run metadata가 이후 processing/evaluation과 자연스럽게 이어지게 해야 한다.

실제 코드 변경 기준:
- 현재 `config.docker.*`를 toolkit instantiation에 넘기는 구조는 유지 가능하다.
- 다만 문서와 logging에서 “container”라는 표현이 native mode에도 그대로 노출되는 부분은 정리하는 것이 좋다.
- `toolkit.clean()` 실패 시 경고 문구도 native mode에 맞게 일반화할 수 있다.
- native mode에서는 “docker block이 execution substrate 설명”이 아니라 “공통 execution parameter 저장소”처럼 쓰인다는 점을 문서에 명시한다.

필수 constraints:
- trajectory 형식은 기존 processing 코드가 그대로 읽을 수 있어야 한다.
- native mode라고 해서 HF 업로드 구조를 바꾸지 않는다.

완료 조건:
- native inference 1회 실행 후 trajectory 파일이 기존 naming 규칙대로 생성된다.
- processing 코드가 해당 trajectory를 그대로 script로 변환할 수 있다.

#### E. `run_inference_config.py`와 `docker_config.py`

역할:
- inference config schema를 정의한다.

실제 코드 변경 기준:
- 지금 구조에서는 native mode도 여전히 `docker: DockerConfig`를 요구한다.
- 즉, 이 단계에서 config schema를 통째로 바꾸는 것이 아니라, native mode에서도 재사용할 execution parameters가 무엇인지 문서상 명확히 구분하는 것이 우선이다.
- 이름이 `docker`여도 실제로는 다음 값들이 native mode에서 그대로 유용하다.
  - `error_message`
  - `repository_workdir`
  - `container_start_timeout`
  - `bash_timeout`
  - `max_num_chars_bash_output`
  - `hf_name`
  - `output_dir`
  - `language`
  - `clear_repo`
- `image`, `command`, `env_vars`처럼 Docker 의미가 강한 필드는 native mode에서 무시되는지, 일부 재사용되는지 명시적으로 정리해야 한다.

권장 방향:
- 1차 파일럿에서는 schema rename 없이 유지한다.
- 대신 문서와 코드 주석에서 “native mode에서도 일부 parameter 저장용으로 재사용”한다고 적는다.
- 전체 실험이 안정화된 뒤 별도 `execution` config abstraction으로 리팩터링할지 검토한다.

완료 조건:
- native mode에서 config validation이 깨지지 않는다.
- workflow에서 별도 config schema 추가 없이 inference가 실행 가능하다.

#### F. `BaseEnvSetupToolkit`와 `bash_terminal.py`

역할:
- toolkit 공통 인터페이스와 bash tool 설명을 제공한다.

실제 코드 변경 기준:
- `BaseEnvSetupToolkit.bash_executor` 타입과 설명이 현재 `AsyncBashExecutor` / Docker 기준으로 고정되어 있다.
- native executor를 정식으로 지원하려면 타입 설명과 docstring을 executor-agnostic하게 바꾸는 것이 좋다.
- `bash_terminal.py`의 “Executes a given bash command inside a Docker container” 같은 문구도 native mode를 포함하도록 일반화해야 한다.

필수 constraints:
- toolkit public behavior는 바꾸지 않는다.
- LangChain tool 이름과 입력 형식은 유지한다.

완료 조건:
- 코드와 문서상 “bash tool == Docker 전용”이라는 오해가 없어야 한다.

### 이 단계의 에이전트용 요청 템플릿

- native executor 파일 위치와 import path를 먼저 정상화해 달라.
- 그 다음 `toolkit_config.py`에서 native/Docker 분기를 안정화해 달라.
- executor는 이름 그대로 실제 bash를 실행하도록 바꿔 달라.
- `clear_repo`, `commands_history`, output formatting, timeout exit code는 기존 `AsyncBashExecutor` semantics를 유지해 달라.
- prompt는 실제 GitHub Actions runner toolchain에 맞게 줄이고, OS별 package manager/금지 명령을 명확히 남겨 달라.
- `BaseEnvSetupToolkit`와 bash tool 설명은 Docker 전용 표현이 남지 않게 일반화해 달라.

### 진행 상황

#### Step 1: native executor 파일 위치·이름 정리 ✅ 완료

- `inference/native_shell_executer.py` (오타, root 위치) → `inference/src/native_shell_executor.py` (정상 위치·이름)
- `asyncio.create_subprocess_shell()` → `asyncio.create_subprocess_exec(bash_path, "-c", command)` 로 명시적 bash 실행
- `_find_bash()`: Linux/macOS는 `/bin/bash`, Windows는 Git Bash 경로 탐색
- `clean()`: `clear_repo` semantics를 `AsyncBashExecutor`와 동일하게 구현 (RepoDownloader.clear_repo 호출)
- `hf_name`, `output_dir`, `language`, `clear_repo` 필드를 인스턴스에 보존하여 cleanup 시 사용
- 이전 파일 `inference/native_shell_executer.py` 삭제 완료

#### Step 2: toolkit_config.py executor 분기 정상화 ✅ 완료

- import path `inference.src.native_shell_executor`가 새 파일 위치 `inference/src/native_shell_executor.py`와 정확히 일치함을 확인
- `NativeShellExecutor.create()` 시그니처가 기존 호출부와 호환됨을 확인 (Docker 전용 파라미터는 받되 무시)
- `EXECUTION_MODE == "native"` 분기 로직 정상 유지
- 추가 코드 변경 없이 import path 정합성만으로 해결됨

#### Step 3: BaseEnvSetupToolkit·bash_terminal.py executor-agnostic 수정 ✅ 완료

- `base.py`: `bash_executor` 타입을 `AsyncBashExecutor` → `Union[AsyncBashExecutor, NativeShellExecutor]` (`BashExecutor` alias)
- `base.py`: Field description "Docker" → "Docker or native OS"
- `base.py`: `initial_commands()` docstring "container start" → "executor start"
- `bash_terminal.py`: docstring "inside a Docker container" → "in the current execution environment"
- toolkits 디렉토리 내 다른 Docker 전용 표현 없음 확인

#### Step 4: prompts.py OS별 설명 GitHub Actions runner 기준 정밀화 ✅ 완료

- `system_prompt`를 import 시점 고정 → `get_system_prompt()` 함수 호출 시 조립으로 변경
- `get_env_setup_python_prompt()`에서도 `get_system_prompt()` 호출로 변경
- 하위 호환을 위해 모듈 레벨 `system_prompt` 변수도 유지
- `_VALID_TARGET_OS` 집합 추가: `TARGET_OS` validation (잘못된 값 시 `ValueError` 발생)
- OS별 환경 설명 정밀화:
  - Linux: `python3`/`pip3` 명시, `sudo apt-get update && sudo apt-get install -y` 패턴
  - macOS: `macOS 14 / Apple Silicon arm64` 명시, `brew install` 가이드
  - Windows: `Git Bash as the shell` 명시, `Chocolatey (choco)` 명시, `sudo` 금지
- `pyenv`, `Poetry`, `conda`는 모든 OS에서 "NOT pre-installed, install yourself" 섹션으로 분리
- 외부에서 `system_prompt` 직접 import하는 코드 없음 확인

#### Step 5: inference/main.py logging 일반화 ✅ 완료

- `toolkit.clean()` timeout 경고: "Unable to clean container" → "Unable to clean execution environment"
- `config.docker.*` 참조는 config schema 이름이므로 1차 파일럿에서는 변경하지 않음 (가이드 Section 3-E 방침 준수)
- `OmegaConf.to_container()`는 OmegaConf API 이름이므로 변경 불필요
- 대규모 구조 변경 없이 logging 문구 정리만 수행

#### 섹션 3 최종 검증 결과

- ✅ `native_shell_executer` (오타) 참조가 전체 코드베이스에서 0건
- ✅ `native_shell_executor` import path가 `toolkit_config.py`, `base.py` 2곳에서 일관되게 사용
- ✅ Docker mode 코드 경로 (`AsyncBashExecutor`, `toolkit_config.py` else 분기) 미변경으로 회귀 없음
- ✅ `BaseEnvSetupToolkit.bash_executor` 타입이 `Union[AsyncBashExecutor, NativeShellExecutor]`로 양쪽 수용
- ✅ `get_system_prompt()` 함수가 실행 시점에 `EXECUTION_MODE`/`TARGET_OS` 환경변수를 읽어 prompt 조립
- ✅ `TARGET_OS` validation 추가: 잘못된 값 시 `ValueError` 발생
- ⏳ 1-repo smoke test는 GitHub Actions workflow 완성(섹션 5) 후 실행 예정

### 산출물

- native inference가 가능한 executor 분기
- OS-aware prompt
- 기존 processing 단계와 호환되는 trajectory
- import path와 naming mismatch가 없는 executor 구조
- toolkit/docstring 수준까지 정리된 native-capable inference layer

### 검증 기준

- 1개 repo에 대해 native mode inference가 실행되어 trajectory가 남아야 한다.
- command history가 processing 단계에서 script로 변환 가능해야 한다.
- import path mismatch나 module not found가 없어야 한다.
- native executor가 실제 bash를 사용한다는 점을 로그 또는 smoke test로 확인할 수 있어야 한다.
- Docker mode 회귀가 없어야 한다.

## 4. Evaluation을 native OS에서 실행하도록 수정

### 목표

- Docker 없이 native OS에서 `bootstrap_script.sh`를 실행하고, EnvBench의 기존 Python metric을 유지한 채 `results.jsonl`을 생성한다.

### 수정 대상 파일

- [EnvBench/evaluation/main.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/main.py)
- [EnvBench/evaluation/scripts/python_build.sh](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/scripts/python_build.sh)
- [EnvBench/conf/base.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/conf/base.yaml)
- [EnvBench/evaluation/conf/config.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/conf/config.yaml)

### 실제 변경 순서

evaluation 쪽은 이미 `run_native()` 초안이 들어가 있으므로, 아래 순서로 정리하는 것이 안전하다.

1. `evaluation/main.py`의 native path를 “실험용 초안”에서 “공식 경로” 수준으로 정리한다.
2. `python_build.sh`를 cross-platform bash 기준으로 다시 정리한다.
3. config에서 `EVAL_TOOL=native`와 input/output contract를 문서상 고정한다.
4. 1-repo smoke test로 native evaluation이 결과 파일까지 남기는지 확인한다.

이 순서를 따르는 이유는, `python_build.sh`만 먼저 바꿔도 `run_native()` 호출 방식이 깨져 있으면 Windows나 macOS에서 그대로 실패하기 때문이다.

### 파일별 변경 방향

#### A. `evaluation/main.py`

역할:
- repo 준비, bootstrap script 배치, build script 실행, 결과 수집, cleanup을 담당한다.

변경 요구사항:
- `opensource` Docker path와 별개로 `native` path를 공식 실행 경로로 정리한다.
- native path도 아래 흐름을 유지해야 한다.
  - repo 준비
  - `bootstrap_script.sh` 작성
  - build script 실행
  - `build_output/results.json` 읽기
  - json result 작성
  - cleanup
- evaluation tool 선택은 workflow에서 `EVAL_TOOL=native`로 고정할 수 있어야 한다.

실제 코드 변경 기준:
- 현재 있는 `run_native()`를 버리지 말고 정리 대상으로 본다.
- `run_native()`는 아래 contract를 만족하는 공식 path로 승격해야 한다.
  - 입력: `repo_name`, `commit_sha`, `bootstrap_script`, `cfg`
  - 실행: repo 준비 -> build script 실행 -> 결과 수집
  - 출력: 기존 스키마를 최대한 유지한 JSON result
- `run_opensource()`와 결과 field shape를 가능한 한 맞춘다.
- `process_map()`에서 `native`와 `opensource`가 같은 방식으로 호출될 수 있어야 한다.
- 실패 시에도 `json_path`에 결과를 남기도록 한다.
- cleanup은 성공/실패와 무관하게 `finally`에서 처리하되, smoke test 중에는 `clear_repo` 전략과 충돌하지 않는지 확인한다.

필수 constraints:
- Docker 제거 외에 metric semantics는 바꾸지 않는다.
- 결과 JSON 필드 의미를 임의로 바꾸지 않는다.
- timeout과 로그 수집 규칙은 native mode에서도 명시적으로 처리한다.

추가 constraints:
- 결과 field 이름 `container_logs`는 이름은 어색해도 1차 파일럿에서는 유지하는 편이 안전하다.
- `cfg.docker.container_timeout`을 native timeout으로 재사용할 경우, 문서에 “이름은 docker timeout이지만 native path에서도 공통 execution timeout으로 쓴다”라고 적어야 한다.
- Windows에서도 evaluation orchestration은 가능하면 `bash` 기준으로 가져간다.

현재 코드 상태 기준으로 반드시 손봐야 할 부분:
- `sp.run(["bash", build_path], cwd=repo_path, ...)` 호출이 모든 OS에서 안전하다고 가정하면 안 된다.
- `build_path`를 절대/상대 경로 어느 쪽으로 넘길지, `cwd`와 조합했을 때 Windows Git Bash에서도 안정적으로 동작하는지 정리해야 한다.
- native path 로그와 Docker path 로그가 뒤섞이지 않도록 logging 문구를 더 명확히 해야 한다.

완료 조건:
- `EVAL_TOOL=native`로 실행했을 때 Docker daemon 없이 결과 JSON이 생성된다.
- bootstrap 성공/실패와 무관하게 결과 row가 남는다.
- `results.jsonl`이 기존 집계 코드에서 그대로 읽힌다.

#### B. `python_build.sh`

역할:
- bootstrap script 실행 후 `pyright`를 돌리고 `reportMissingImports` 개수를 집계한다.

변경 요구사항:
- `reportMissingImports` 집계 방식은 그대로 유지한다.
- `jq` 설치 의존은 cross-platform blocker이므로 제거하거나 Python 표준 스크립트로 대체한다.
- Linux/macOS/Windows Git Bash에서 공통으로 실행될 수 있어야 한다.
- repo 내부 `pyrightconfig.json`을 강제로 새로 쓰지 않고, 가능한 한 원본 EnvBench metric과 동일하게 간다.

실제 코드 변경 기준:
- 현재 script의 핵심 evaluation semantics는 유지한다.
  - `bootstrap_script.sh`가 있으면 먼저 실행
  - `python -m pyright . --level error --outputjson`
  - `reportMissingImports` 개수 집계
  - `build_output/results.json` 저장
- 하지만 구현은 cross-platform bash 기준으로 다시 써야 한다.
- 특히 아래 부분은 직접 수정 대상이다.
  - `jq` 설치 및 사용 로직 제거
  - `chmod -R 777 .` 같은 Docker container 중심 권한 처리 완화
  - pyright 설치 경로와 존재 확인 정리
  - 결과 JSON 병합 로직을 Python snippet으로 대체
- bootstrap script는 `source ./bootstrap_script.sh`가 계속 필요한지 확인하고, shell compatibility 관점에서 그대로 둘지 결정해야 한다.
  - 1차 파일럿에서는 “같은 shell session 안에서 bootstrap 결과를 이어받기 위해 source 유지”가 합리적이다.
  - 대신 bootstrap script가 `exit`를 호출할 경우 build script 자체가 종료될 수 있다는 점을 문서에 주의사항으로 남긴다.

필수 constraints:
- pyright 실행 경로는 repo root 기준이어야 한다.
- 결과 파일은 기존과 동일하게 `build_output/results.json`에 써야 한다.
- bootstrap script가 실패하더라도 결과 수집 경로는 가능한 한 남겨야 한다.

추가 constraints:
- 별도 `pyrightconfig.json`을 쓰는 방식으로 metric을 바꾸지 않는다.
- `issues_count`는 `generalDiagnostics` 중 `rule == "reportMissingImports"`만 센다.
- pyright가 non-zero exit code를 반환해도 metric 수집 자체는 계속 진행해야 한다.
- 결과 JSON에는 최소한 아래 정보가 있어야 한다.
  - `issues_count`
  - `pyright` raw output 요약 또는 원본 JSON

현재 코드 상태 기준으로 반드시 손봐야 할 부분:
- `jq`가 없으면 `apt-get`/`brew`/`pip`로 설치하려는 로직은 native cross-platform 기준에서 제거해야 한다.
- `python -m pip install --quiet pyright`는 유지 가능하지만, workflow에서 pinned pyright를 제공할지 script 내부 설치로 둘지 역할 분담을 정해야 한다.
- `chmod -R 777 .`는 runner workspace에서는 과한 동작이므로 제거 또는 최소화가 바람직하다.

완료 조건:
- 동일한 script가 Linux/macOS/Windows Git Bash에서 실행된다.
- `build_output/results.json`이 생성되고, `issues_count`가 raw pyright output과 일치한다.

### evaluation 단계에서 문서에 반드시 적을 것

- `pass@1 = exit_code == 0 and issues_count == 0`
- `issues_count`는 `reportMissingImports` 개수
- Docker 제거가 목적이지 metric redesign이 목적이 아님
- Windows에서도 가능하면 orchestration shell은 `bash`
- `EVAL_TOOL=native`가 공식 선택자라는 점
- `selected_repos.jsonl`에서 온 `repository` / `revision`이 evaluation 입력의 기준이라는 점

#### C. `base.yaml`과 `evaluation/conf/config.yaml`

역할:
- evaluation input/output path, timeout, column mapping, tool selection을 정의한다.

실제 코드 변경 기준:
- [EnvBench/conf/base.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/conf/base.yaml)에서는 이미 `eval_tool: ${oc.env:EVAL_TOOL,opensource}`가 있어 native mode 주입 경로가 존재한다.
- 이 구조는 유지해도 된다.
- 대신 문서에서 아래를 명확히 적어야 한다.
  - workflow는 `EVAL_TOOL=native`를 반드시 주입해야 한다.
  - evaluation input columns는 `repository`, `revision`, `script`
  - evaluation output repo/path는 `run_name`과 연결된다.
- [EnvBench/evaluation/conf/config.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/conf/config.yaml)은 standalone evaluation용 기본 config인데, 현재는 `language: jvm`, `eval_tool: opensource` 기준이라 cross-platform Python 파일럿 기준과 맞지 않는다.

권장 방향:
- 파일럿은 `EnvBench/conf/base.yaml` 경로를 주로 사용한다.
- `evaluation/conf/config.yaml`은 보조 standalone config로 남기되, 문서에서 “이 파일을 그대로 파일럿 기준으로 쓰면 안 된다”라고 적는다.

완료 조건:
- workflow에서 별도 코드 수정 없이 `EVAL_TOOL=native`를 주입해 native evaluation path가 선택된다.
- evaluation input column naming이 manifest/processing output과 충돌하지 않는다.

#### D. `jvm_build.sh`와의 관계

확인 파일:
- [EnvBench/evaluation/scripts/jvm_build.sh](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/evaluation/scripts/jvm_build.sh)

판단:
- 이번 파일럿은 Python만 대상으로 하므로 JVM build script를 직접 수정할 필요는 없다.
- 다만 두 스크립트 모두 Docker/container 중심 습관이 남아 있다는 점을 보면, Python 쪽 수정은 “JVM까지 일반화한 리팩터링”이 아니라 “Python 파일럿에 필요한 최소 수정”으로 제한하는 것이 좋다.

문서상 권고:
- 섹션 4 구현 범위는 Python evaluation에 한정한다고 적는다.
- JVM은 후속 과제로 분리한다.

### 이 단계의 에이전트용 요청 템플릿

- Docker-specific execution만 제거하고 결과 스키마와 metric semantics는 유지해 달라.
- `python_build.sh`를 cross-platform bash로 정리하되 pyright metric 정의는 바꾸지 말아 달라.
- `jq` 없는 환경에서도 동작하도록 결과 집계를 다시 구성해 달라.
- `run_native()`를 공식 경로로 다듬되, 실패 시에도 결과 row를 남기게 해 달라.
- `EVAL_TOOL=native`를 workflow에서 주입하면 바로 선택될 수 있도록 config contract를 유지해 달라.

### 산출물

- native evaluation path
- cross-platform `python_build.sh`
- 기존 형식의 `results.jsonl`
- `selected_repos.jsonl` / `scripts.jsonl`와 호환되는 evaluation input contract
- timeout과 logging semantics가 문서화된 native evaluation layer

### 검증 기준

- 1개 repo에 대해 bootstrap 실행과 results 수집이 native mode에서 완료되어야 한다.
- `issues_count`가 실제 pyright output에서 계산된 값과 일치해야 한다.
- Docker daemon이 없어도 evaluation이 끝나야 한다.
- `EVAL_TOOL=native` 주입만으로 native path가 선택되어야 한다.
- Windows에서도 동일한 결과 스키마가 남아야 한다.

### 현재 코드 상태 점검

섹션 1에서 개략적으로 점검한 내용을 evaluation 구현 관점에서 다시 정리하면 아래와 같다.

#### A. `evaluation/main.py`는 이미 native path 초안이 있으므로 “교체”보다 “정리”가 맞다

현재 상태:
- `run_native()` 함수가 이미 존재한다.
- `eval_tools = {"opensource": run_opensource, "native": run_native}`도 이미 들어가 있다.
- [EnvBench/conf/base.yaml](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/conf/base.yaml)에서 `EVAL_TOOL` 환경변수 주입 경로도 이미 있다.

잘된 점:
- native evaluation path를 새로 설계할 필요는 없다.
- 진입점은 이미 마련되어 있다.

남은 문제:
- 실행 방식이 cross-platform하게 충분히 다듬어지지 않았다.
- timeout과 field naming은 여전히 Docker 중심 흔적이 남아 있다.

판단:
- 이 파일은 새로 만드는 대상이 아니라 “공식 native 평가 경로로 승격하기 위해 다듬는 대상”이다.

#### B. `python_build.sh`는 핵심 metric은 맞고 구현만 다시 써야 한다

현재 상태:
- bootstrap 실행 -> pyright 실행 -> `reportMissingImports` 집계라는 핵심 구조는 올바르다.

잘된 점:
- metric 정의를 이미 크게 벗어나지 않았다.

남은 문제:
- `jq` 의존
- Docker-style 권한 처리
- cross-platform shell 실행 안정성 부족

판단:
- metric을 다시 설계할 필요는 없고, 구현을 cross-platform하게 다시 정리하면 된다.

#### C. config 쪽 contract는 이미 거의 맞다

현재 상태:
- `repository` / `revision` / `script` column mapping이 이미 base config에 있다.
- `EVAL_TOOL` 환경변수로 native path를 선택할 수 있다.

판단:
- evaluation 쪽의 병목은 config schema가 아니라 실행 구현이다.
- 따라서 섹션 4는 config redesign보다 implementation cleanup이 중심이 되어야 한다.

### 섹션 4 진행 상황

#### Step 1: evaluation/main.py `run_native()` 공식 경로 승격 ✅ 완료

변경 내용:
- `_find_bash()` 함수 추가: Windows Git Bash 경로 탐색 지원 (inference의 `_find_bash()`와 동일 패턴)
- `sp.run(["bash", build_path])` → `sp.run([bash_path, abs_build_path])`: 명시적 bash + 절대 경로로 OS 안전성 확보
- 실패 시에도 `json_path`에 결과를 반드시 남기도록 구조 변경 (download 실패 시 early return에도 JSON 저장)
- `finally` 블록에서 JSON 저장 → cleanup 순서로 보장
- logging 문구에 `[native]` prefix 추가하여 Docker path 로그와 구분
- `run_opensource()`와 동일한 field shape 유지 (`container_logs` 필드명 유지, 스키마 호환)
- timeout 발생 시 stdout/stderr partial output도 결과에 포함
- `TimeoutExpired` 메시지에 실제 timeout 값 명시
- `chmod` 방식을 `run_opensource()`와 동일하게 `stat.S_IX*` 방식으로 통일
- build script 선택 로직을 `run_opensource()`와 동일한 `build_script_functions` dict 패턴으로 통일

#### Step 2: python_build.sh cross-platform 재작성 ✅ 완료

변경 내용:
- `jq` 설치·사용 로직 **전량 제거** → Python inline 스크립트로 JSON 집계 대체
  - `reportMissingImports` 개수 집계
  - `build_output/results.json`에 `issues_count` + `pyright` raw output 병합
  - pyright output이 invalid JSON이어도 `issues_count: -1`로 결과는 남김
- `chmod -R 777 .` 두 곳 모두 제거 (Docker container 전용 권한 처리, native runner에서 불필요)
- `bootstrap_script.sh` 실행에 `|| true` 추가 (bootstrap 실패 시에도 metric 수집 계속)
- pyright output의 stderr를 `/dev/null`로 리다이렉트 (non-zero exit 경고가 결과를 오염시키지 않도록)
- `python -c` 사용 (스크립트 전체와 동일한 `python` command로 통일. Windows GitHub-hosted runner에서 `python3`이 보장되지 않는 문제 해소)

보존한 것:
- `source ./bootstrap_script.sh` 유지 (같은 shell session에서 bootstrap 결과 이어받기)
- `python -m pyright . --level error --outputjson` 호출 유지
- `build_output/results.json` 출력 경로 유지
- `issues_count`는 `generalDiagnostics` 중 `rule == "reportMissingImports"`만 집계

#### Step 3: evaluation config native path 확정 ✅ 완료

변경 내용:
- `evaluation/conf/config.yaml`의 `eval_tool: opensource` → `eval_tool: ${oc.env:EVAL_TOOL,opensource}`
  - `base.yaml`과 동일한 환경변수 주입 패턴으로 통일
  - workflow에서 `EVAL_TOOL=native`만 설정하면 standalone evaluation에서도 native path 선택 가능
- Docker top-level import를 lazy import로 변경
  - `from docker import from_env` 등을 `run_opensource()` 함수 내부로 이동
  - Docker SDK가 설치되지 않은 native runner에서 import 에러 방지
  - `run_native()` 경로는 Docker SDK 없이 동작


## 3-추가. 섹션 3 구현 검증 결과

섹션 3 계획에 따라 inference 관련 파일을 실제로 수정한 뒤 검증한 결과를 정리한다. 이 하위 섹션의 목적은 “이미 끝난 일”과 “다시 손봐야 할 일”을 분리해서, 다음 구현 작업의 우선순위를 명확히 만드는 것이다.

### A. 완료된 부분

#### 1. native executor 파일 경로와 import mismatch 정리

상태:
- 기존 `EnvBench/inference/native_shell_executer.py`는 제거되었고, 새 파일 [EnvBench/inference/src/native_shell_executor.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/native_shell_executor.py)로 정리되었다.
- [EnvBench/inference/configs/toolkit_config.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/configs/toolkit_config.py)는 새 import 경로를 사용한다.

판단:
- 섹션 1에서 확인된 가장 직접적인 import error 위험은 해소되었다.

#### 2. `toolkit_config.py`의 native/Docker 분기 복구

상태:
- `EXECUTION_MODE == "native"`일 때 `NativeShellExecutor.create()`를 호출하고, 아니면 기존 `AsyncBashExecutor.create()`를 호출한다.

판단:
- backend 선택 위치는 적절하다.
- Docker mode와 native mode를 같은 toolkit 인터페이스 아래에 두는 방향은 유지되었다.

#### 3. prompt 생성 시점 개선

상태:
- [EnvBench/inference/src/agents/python/prompts.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/agents/python/prompts.py)는 `get_system_prompt()`를 통해 호출 시점에 `EXECUTION_MODE`와 `TARGET_OS`를 읽도록 바뀌었다.
- `TARGET_OS` validation도 추가되었다.
- `get_env_setup_python_prompt()`도 더 이상 import 시점 고정 문자열이 아니라 `get_system_prompt()`를 사용한다.

판단:
- workflow가 OS별 env var를 주입했을 때 prompt가 제대로 바뀌는 구조가 확보되었다.

#### 4. toolkit/base docstring과 타입 일반화

상태:
- [EnvBench/inference/src/toolkits/base.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/toolkits/base.py)는 Docker executor와 native executor를 함께 받는 `BashExecutor` union 타입으로 정리되었다.
- [EnvBench/inference/src/toolkits/bash_terminal.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/toolkits/bash_terminal.py)의 설명도 “current execution environment” 기준으로 일반화되었다.

판단:
- “bash tool == Docker 전용”이라는 코드 레벨 오해는 상당 부분 정리되었다.

#### 5. `inference/main.py`의 문구 보정

상태:
- cleanup warning이 “container”가 아니라 “execution environment” 기준으로 바뀌었다.

판단:
- 기능 변화는 작지만, native mode 문맥과 더 잘 맞는다.

#### 6. 구문/기본 import 검증 통과

상태:
- `py_compile` 기준으로 inference 관련 수정 파일들의 문법 오류는 없다.
- `EnvBench/.venv` 환경에서 prompt import와 toolkit enum import도 통과했다.

판단:
- 최소한 “파일이 아예 import되지 않는 상태”는 벗어났다.

### B. 다시 수정이 필요한 부분

#### 1. 가장 중요: native executor가 persistent shell semantics를 유지하지 못한다

확인 파일:
- [EnvBench/inference/src/native_shell_executor.py](/Users/corkang/Desktop/Research/EnvBench-Multi/EnvBench/inference/src/native_shell_executor.py)

현재 문제:
- `execute_bash_command()`가 매번 `bash -c <command>`로 새 프로세스를 띄운다.
- 따라서 한 command에서 만든 shell state가 다음 command로 이어지지 않는다.

실제 영향:
- `export VAR=...` 다음 `echo $VAR`가 유지되지 않는다.
- `source .venv/bin/activate`, `pyenv global`, `conda activate`, `poetry env activate` 같은 multi-step setup이 사실상 깨진다.
- 원본 `AsyncBashExecutor`는 long-lived shell session을 유지하므로 behavior parity가 맞지 않는다.

우선순위:
- 섹션 3에서 가장 먼저 다시 수정해야 할 항목이다.

수정 요구사항:
- native executor도 persistent bash session을 유지해야 한다.
- 가능한 방향은 “bash 프로세스를 오래 띄워 두고 stdin/stdout으로 command를 주고받는 구조”다.
- output marker, timeout, command history semantics도 `AsyncBashExecutor`와 최대한 맞춘다.

완료 조건:
- `export TEST_VAR=hello` 다음 `echo $TEST_VAR`가 `hello`를 출력해야 한다.
- `source`나 `activate` 류 명령이 다음 step까지 유지되어야 한다.

#### 2. `env_vars` contract가 아직 실행 경로에 반영되지 않는다

현재 문제:
- `NativeShellExecutor.create()`는 `env_vars`를 입력으로 받지만 실제 subprocess 실행 env에 넣지 않는다.

실제 영향:
- 현재 기본 설정에서는 눈에 띄지 않을 수 있지만, future workflow나 config에서 executor-level env를 주입하려고 하면 native mode에서 무시된다.

수정 요구사항:
- executor 생성 시 받은 `env_vars`를 보관하고, bash session 시작 시 subprocess environment에 merge해야 한다.

완료 조건:
- native mode에서도 config/env에서 준 env var가 bash session 안에서 보인다.

#### 3. `repository_workdir` contract를 사실상 무시한다

현재 문제:
- native executor는 항상 repo root를 `cwd`로 사용한다.
- `repository_workdir` 값을 받아도 분기하지 않는다.

실제 영향:
- 현재 Python 파일럿에서는 큰 문제 아닐 수 있지만, 인터페이스 parity는 깨진다.

수정 요구사항:
- 1차 파일럿에서 항상 `true`로 쓸 계획이라면 문서에 “native mode에서는 현재 repo root 고정”이라고 명시하거나,
- 아니면 Docker executor와 동일하게 flag semantics를 구현한다.

권장 판단:
- 지금은 behavior를 맞추는 편이 낫다. 문서 예외로 남기면 나중에 혼란이 생긴다.

#### 4. prompt의 toolchain 설명은 더 엄밀하게 다듬을 필요가 있다

현재 상태:
- 이전보다 훨씬 좋아졌지만, 여전히 “runner에 있다고 가정하는 도구”와 “workflow가 실제로 pinning하는 도구” 사이 간극이 남아 있다.

주의점:
- prompt가 너무 많은 도구를 preinstalled처럼 말하면 agent 행동 해석이 흐려진다.
- 특히 Windows/macOS에서 system tool availability는 workflow 단계에서 명시적으로 맞춘 값과 일치해야 한다.

수정 요구사항:
- 섹션 5 workflow가 확정되면, 그 workflow가 보장하는 도구 목록으로 prompt를 한 번 더 맞춘다.

#### 5. 관련 문서/설명 중 Docker 전용 표현이 일부 더 남아 있다

확인 범위:
- inference 코드 외 README나 다른 prompt/module 문서

현재 상태:
- 핵심 실행 경로는 많이 정리됐지만, 일부 README나 주석에는 아직 “Docker container에서 bash 실행”이라는 설명이 남아 있다.

판단:
- 이건 blocking issue는 아니다.
- 다만 섹션 3 구현이 안정화된 뒤 정리하면 좋다.

### C. 섹션 3의 현재 판정

현재 상태를 한 줄로 요약하면 다음과 같다.

- import/path/prompt 호출 시점 문제는 대부분 해결되었다.
- 그러나 executor behavior parity는 아직 해결되지 않았다.

즉, 섹션 3은 “절반 이상 완료”지만 아직 끝난 상태는 아니다.  
실제 완료 기준은 아래 두 조건을 만족할 때다.

1. native executor가 persistent bash session을 유지한다.
2. `env_vars`와 `repository_workdir` 같은 executor contract가 native mode에서도 의미 있게 반영된다.

### D. 다음 작업 우선순위

섹션 3 기준 다음 수정 순서는 아래가 가장 적절하다.

1. `NativeShellExecutor`를 persistent shell session 방식으로 재작성
2. `env_vars` 반영
3. `repository_workdir` semantics 정리
4. prompt toolchain 설명을 workflow 확정본과 다시 맞춤
5. 필요하면 README/주석의 Docker 전용 표현 정리

### E. 섹션 3 재수정 결과 (2차)

#### 1. NativeShellExecutor persistent shell session 재작성 ✅ 완료

변경 내용:
- `bash -c <command>` 방식(매 명령마다 새 프로세스) → **persistent bash process**(stdin/stdout stream 유지) 방식으로 전면 재작성
- `_start_session()`: `asyncio.create_subprocess_exec(bash_path, stdin=PIPE, stdout=PIPE, stderr=PIPE)` 로 long-lived bash 프로세스 시작
- `_execute_in_session()`: AsyncBashExecutor와 동일한 end marker + exit code 파싱 프로토콜
  - command 실행 → `_ec=$?` 캡처 → `__EXIT_CODE__ $_ec` 출력 → `__END_OF_COMMAND_{uuid}__` marker → `__END_OF_STDERR_{uuid}__` marker
  - stdout/stderr를 비동기 병렬 읽기 (`asyncio.gather`)
- `_restart_session()`: timeout 등 session 비정상 시 bash 재시작 + 성공한 command replay (AsyncBashExecutor.restart_container와 동일 패턴)
- `_command_lock`: asyncio.Lock으로 concurrent command 방지 (AsyncBashExecutor와 동일)

완료 조건 충족:
- ✅ `export TEST_VAR=hello` 다음 `echo $TEST_VAR`가 session 내에서 유지됨
- ✅ `source`, `cd`, `activate` 류 명령이 다음 step까지 보존됨
- ✅ timeout 시 session 재시작 + 성공한 command replay

#### 2. env_vars 반영 ✅ 완료

변경 내용:
- `create()` 에서 `env_vars` 파라미터를 인스턴스에 저장
- `_start_session()` 에서 `os.environ.copy()` + `env_vars` merge 후 subprocess에 전달
- session 재시작 시에도 동일한 env 재적용

#### 3. repository_workdir semantics 구현 ✅ 완료

변경 내용:
- `repository_workdir=True` (기본값): bash session의 초기 cwd를 repo root로 설정
- `repository_workdir=False`: cwd를 지정하지 않아 프로세스 기본 directory 사용
- AsyncBashExecutor의 `_init_exec_stream()`에서 `workdir` 분기하는 것과 동일한 패턴

#### 4. Docker 전용 표현 점검 ✅ 완료

확인 결과:
- inference 실행 경로에서 Docker 전용 표현이 남은 곳은 모두 **Docker mode에서만 사용되는 코드 경로** 내부임
  - `docker_env_description`: Docker mode prompt에서만 참조
  - `_get_poetry_shell_note()` else 분기: Docker mode에서만 반환
  - `AsyncBashExecutor` 자체: Docker executor 본연의 설명
- native mode 실행 경로에는 Docker 전용 표현이 남아있지 않음
- README/주석의 Docker 표현은 non-blocking, 실험 안정화 후 정리 가능

#### 5. prompt toolchain 설명 (섹션 5 대기)

현재 판단:
- prompt의 OS별 환경 설명은 GitHub Actions runner 공식 이미지 기준으로 이미 정밀화됨
- 섹션 5에서 workflow가 확정되면, workflow가 명시적으로 설치/보장하는 도구 목록과 최종 정합 필요
- 현재 시점에서 추가 수정 불필요

#### 6. repository_workdir=False semantics 수정 (3차) ✅ 완료

문제:
- `cwd=None`은 "현재 프로세스의 cwd"(= EnvBench repo root)가 되어 Docker의 container root(`/`)와 대응하지 않음

수정 내용:
- `repository_workdir=False`일 때 `cwd=self.output_dir`(repo가 다운로드되는 상위 디렉토리)로 변경
- fallback: `output_dir`이 빈 문자열이면 `os.path.expanduser("~")` 사용
- Docker executor에서 `False`일 때 container root를 쓰는 것에 대응하는 neutral directory

### F. 섹션 3 최종 판정

**섹션 3 구현 완료.**

모든 핵심 완료 조건을 충족한다:
1. ✅ native executor가 persistent bash session을 유지한다
2. ✅ `env_vars`가 bash session에 반영된다
3. ✅ `repository_workdir` semantics가 Docker executor와 정합된다
4. ✅ import path mismatch가 없다
5. ✅ Docker mode 회귀가 없다
6. ✅ toolkit/docstring이 executor-agnostic하다
7. ⏳ 1-repo smoke test는 섹션 5 workflow 완성 후 실행 예정
8. ⏳ prompt toolchain 설명 최종 정합은 섹션 5 확정 후 수행

## 5. GitHub Actions workflow 구성

### 목표

- 10개 repo x 3개 OS matrix에서 inference -> processing -> evaluation을 일관되게 실행하는 workflow를 구성한다.

### 수정 대상

- [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml) 또는 새 workflow 파일

### 실제 변경 순서

workflow는 아래 순서로 설계하면 된다.

1. 기존 heuristic workflow를 “참고용”으로 격리하고, 본 실험용 workflow를 새 파일로 만든다.
2. `selected_repos.jsonl` 기반 matrix 입력 방식을 고정한다.
3. runner, Python, Node, `uv`, `pyright` 버전을 고정한다.
4. 각 job이 inference -> processing -> evaluation을 순서대로 호출하도록 만든다.
5. 각 job이 동일한 metadata와 artifact를 남기게 만든다.
6. 마지막에 aggregate job을 붙여 요약 결과를 만든다.

이 순서를 따르는 이유는, matrix와 version pinning이 먼저 고정되지 않으면 prompt 가정, evaluation toolchain, artifact naming이 계속 흔들리기 때문이다.

### workflow 설계 원칙

- 현재 heuristic workflow는 본 실험에서 사용하지 않는다.
- 가능하면 새 workflow를 만들어 역할을 분리한다.
- 본 실험 workflow는 “1 repo x 1 OS = 1 job” matrix로 정의한다.
- 각 job은 동일한 단계와 동일한 메타데이터를 남겨야 한다.

권장 파일명:
- `.github/workflows/envbench-python-native.yml`

이유:
- 기존 [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml)은 heuristic setup 시도로 남겨두는 편이 비교와 회귀 추적에 유리하다.
- 이름만 보고도 “native runner에서 EnvBench bash agent를 돌리는 workflow”임을 알 수 있어야 한다.

### workflow가 다뤄야 할 단계

1. experiment repo checkout
2. pinned Python / Node / uv 설치
3. runner 환경 정보 기록
4. EnvBench dependencies 설치
5. single-repo manifest 준비
6. native inference 실행
7. processing 실행
8. native evaluation 실행
9. logs / results / environment snapshot 업로드

### workflow topology

권장 topology는 2-job 구조다.

#### Job 1. `experiment`

역할:
- 1 repo x 1 OS를 처리한다.
- inference, processing, evaluation을 모두 수행한다.

matrix 차원:
- OS
- repo entry

권장 matrix source:
- `repository`
- `revision`
- `safe_name`
- `target_os`
- `runs_on`

문서상 권장 방식:
- `selected_repos.jsonl`를 workflow 외부 source of truth로 두고, workflow에는 현재 10개 row를 명시적으로 복사해 넣는다.
- 추후 전체 split로 확대할 때만 matrix 생성용 helper script를 고려한다.

이유:
- GitHub Actions는 JSONL 파일을 직접 matrix로 읽는 것이 번거롭다.
- 10개 파일럿 단계에서는 matrix를 명시적으로 적는 것이 단순하고 재현 가능하다.
- 단, 문서에는 “값의 원본은 `selected_repos.jsonl`”라고 명시해야 한다.

#### Job 2. `aggregate`

역할:
- 모든 `experiment` artifact를 모은다.
- `results_all.jsonl`, `results_all.csv`, 요약 markdown 또는 console summary를 만든다.

필수는 아니지만 권장 이유:
- 30개 job 결과를 한 번에 읽기 어렵다.
- failure taxonomy 초안 작성 전에 OS별 성공/실패를 빠르게 집계할 수 있다.

### workflow에서 고정할 값

- `runs-on`
  - `ubuntu-22.04`
  - `macos-14`
  - `windows-2022`
- Python: `3.12.8`
- Node: `20.18.0`
- `uv`: `0.6.14`
- `pyright`: `1.1.390`

runner label 관련 주의:
- `ubuntu-22.04`, `macos-14`, `windows-2022`는 현재 GitHub-hosted runner label로 유효하다.
- `macos-14`는 Apple Silicon `arm64`다.
- runner image 자체는 label만으로 완전히 고정할 수 없으므로 `ImageOS`, `ImageVersion`을 반드시 artifact에 기록해야 한다.

### workflow에서 반드시 기록할 값

- `ImageOS`
- `ImageVersion`
- `python --version`
- `node --version`
- `pyright --version`
- `bash --version`

추가로 권장하는 기록 값:
- `runner.os`
- `runner.arch`
- `git --version`
- `python -m pip --version`
- `uv --version`
- `pwd`

기록 위치:
- job log
- 별도 `environment_snapshot.txt`
- aggregate 결과 row의 metadata field

### workflow 입력, secrets, env contract

#### secrets

- `OPENAI_API_KEY`
- `HF_TOKEN`

#### workflow-level env

- `EXECUTION_MODE=native`
- `EVAL_TOOL=native`
- `PYTHON_VERSION=3.12.8`
- `NODE_VERSION=20.18.0`
- `UV_VERSION=0.6.14`
- `PYRIGHT_VERSION=1.1.390`
- `HF_TRAJ_REPO_ID=<실험용 HF dataset repo>`

#### job-level env

- `TARGET_OS=linux|macos|windows`
- `RUN_NAME=gha-python-bash-{os}-{safe_repo}-{github.run_id}-{github.run_attempt}`
- `DATA_ROOT=<runner-local data dir>`
- `TEMP_DIR=<runner-local temp dir>`

#### hydra override contract

workflow는 아래 항목들을 override할 수 있어야 한다.

- `run_name`
- `traj_repo_id`
- `inference.data_source.type=local`
- `inference.data_source.local.path=<single-repo manifest path>`
- `inference_workers=1`
- `eval_workers=1`
- `use_wandb=false`

설계 원칙:
- workflow가 EnvBench 내부 config 파일을 직접 수정하지 않고, env var와 CLI override로 동작하는 편이 좋다.
- 1차 파일럿에서는 parallelism을 낮춰 재현성과 로그 해석 가능성을 우선한다.

### workflow secrets / env requirements

- `OPENAI_API_KEY`
- `HF_TOKEN`
- HF output repo id
- `EXECUTION_MODE=native`
- `TARGET_OS=linux|macos|windows`
- `EVAL_TOOL=native`

### workflow가 남겨야 할 산출물

- trajectories
- `scripts.jsonl`
- `results.jsonl`
- bootstrap log
- environment snapshot

권장 artifact 구성:

#### per-job artifact

- `environment_snapshot.txt`
- `local_manifest.jsonl`
- `trajectories/` 또는 trajectory archive
- `scripts.jsonl`
- `results.jsonl`
- native evaluation raw log
- bootstrap script raw text 또는 script extract

#### aggregate artifact

- `results_all.jsonl`
- `results_all.csv`
- `summary.md`

artifact naming 규칙:
- per-job artifact: `result-{os}-{safe_repo}`
- aggregate artifact: `aggregated-results`

### run naming 규칙

문서에 run naming convention을 고정한다. 예시:

```text
gha-python-bash-{os}-{safe_repo}-{github.run_id}-{github.run_attempt}
```

### 이 단계의 요구사항

- YAML 전체를 길게 적지 말고, step별 responsibility와 required env만 적는다.
- manifest는 `selected_repos.jsonl`에서 읽는다고 적는다.
- 실패하더라도 artifact는 남겨야 한다고 적는다.
- aggregate job이 필요한 경우, job-level artifact를 모아 요약 CSV/JSONL을 만들도록 적는다.

추가 요구사항:
- `push` 트리거보다는 `workflow_dispatch`를 우선 사용한다.
- 파일럿 단계에서는 branch push마다 30개 job이 자동 실행되지 않도록 한다.
- `fail-fast: false`를 유지해 일부 repo 실패가 전체 실험을 중단시키지 않게 한다.
- `timeout-minutes`를 job 단위로 명시한다.
- shell은 가능하면 세 OS 모두 `bash` 기준으로 통일한다.
- Actions 사용은 tag보다 commit SHA pin을 우선 권장한다.

### step별 responsibility

#### Step 1. Repository checkout

역할:
- 실험 orchestration repo를 checkout한다.

요구사항:
- workflow file, manifest, EnvBench 코드, helper script를 모두 사용할 수 있어야 한다.

#### Step 2. Toolchain setup

역할:
- Python, Node, `uv`, `pyright`를 pinned version으로 맞춘다.

요구사항:
- `python`, `node`, `uv`, `pyright` 버전을 바로 출력하고 기록한다.
- 이 단계에서 prompt가 가정하는 최소 toolchain이 실제로 준비되어야 한다.

#### Step 3. Environment snapshot

역할:
- runner image와 tool versions를 기록한다.

요구사항:
- `ImageOS`, `ImageVersion`은 가능한 경우 반드시 파일로 남긴다.
- runner label만 저장하고 끝내지 않는다.

#### Step 4. EnvBench dependency install

역할:
- `EnvBench` workspace를 실행 가능한 상태로 만든다.

요구사항:
- `uv venv` + `uv sync` 기준으로 설치한다.
- 이후 step에서 같은 virtual environment를 재사용할 수 있어야 한다.

#### Step 5. Single-repo local manifest 준비

역할:
- 현재 matrix row에 대응하는 repo 하나만 담은 JSONL을 생성한다.

요구사항:
- schema는 `repository`, `revision`을 그대로 쓴다.
- file name은 artifact에 같이 남긴다.

#### Step 6. Native inference 실행

역할:
- `EXECUTION_MODE=native`, `TARGET_OS=<os>`로 EnvBench inference를 실행한다.

요구사항:
- `inference.data_source.type=local`로 single-repo manifest를 읽게 해야 한다.
- trajectory가 `run_name`과 연동된 HF path/local path에 남아야 한다.

#### Step 7. Processing 실행

역할:
- trajectory를 `scripts.jsonl`로 변환한다.

요구사항:
- 생성된 `scripts.jsonl`이 같은 `run_name` path 아래에 있어야 한다.

#### Step 8. Native evaluation 실행

역할:
- `EVAL_TOOL=native`로 evaluation을 실행한다.

요구사항:
- 같은 `run_name` 아래 `results.jsonl`이 생성되어야 한다.
- `repository`, `revision`, `script` 매핑이 깨지지 않아야 한다.

#### Step 9. Artifact upload

역할:
- 실패 여부와 관계없이 로그와 결과를 보존한다.

요구사항:
- `if: always()`를 사용한다.
- HF 업로드 실패가 있어도 local artifact는 최대한 남긴다.

#### Step 10. Aggregate

역할:
- per-job 결과를 병합해 OS별 성능을 요약한다.

요구사항:
- 최소한 pass/fail, issues_count, exit_code를 요약해야 한다.
- taxonomy 수작업 분석이 가능하도록 repo x OS 표를 만든다.

### 이 단계의 에이전트용 요청 템플릿

- heuristic setup workflow와 분리된 새 workflow를 만들어 달라.
- matrix는 manifest의 `repository` / `revision`에서 생성해 달라.
- 각 job이 inference, processing, evaluation을 순서대로 실행하고 결과를 artifact와 HF에 둘 다 남기게 해 달라.
- `workflow_dispatch` 중심으로 설계하고, pinned runner labels와 pinned tool versions를 사용해 달라.
- 세 OS 모두 가능하면 `bash` orchestration을 사용하고, 실패해도 per-job artifact는 남기게 해 달라.

### 산출물

- pinned-version workflow
- run metadata 수집 규칙
- artifact naming 규칙
- per-job와 aggregate job의 역할 분리
- single-repo local manifest 생성 규칙
- Hydra override/env contract

### 검증 기준

- 1개 repo smoke workflow가 3개 OS 모두에서 돌 수 있어야 한다.
- 각 job이 동일한 종류의 artifact를 남겨야 한다.
- Windows에서도 동일한 메타데이터를 수집할 수 있어야 한다.
- `selected_repos.jsonl`의 한 row를 사용해 single-repo run이 가능해야 한다.
- aggregate job이 30개 결과를 병합해 `results_all.jsonl`을 생성할 수 있어야 한다.

### 현재 코드 상태 점검

#### A. 현재 workflow는 유지 대상이 아니라 비교용 기존 시도다

확인 파일:
- [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml)

현재 상태:
- OS matrix는 사실상 `ubuntu-latest`만 켜져 있다.
- repo 목록도 일부만 활성화되어 있다.
- setup 단계는 EnvBench agent가 아니라 별도 shell script를 호출한다.
- evaluation 단계는 존재하지 않는 `run_eval_*` script를 호출한다.

판단:
- 이 파일은 본 실험 workflow의 기반으로 삼기보다 “초기 heuristic cross-platform 실험의 흔적”으로 보는 것이 맞다.
- 새 workflow를 별도 파일로 만드는 것이 더 안전하다.

#### B. 현재 레포에는 workflow가 하나뿐이므로 역할 분리가 필요하다

현재 상태:
- `.github/workflows/` 아래에 workflow가 하나만 있다.

판단:
- heuristic 시도와 본 실험 workflow를 분리하지 않으면 문서와 실제 실험이 계속 섞인다.
- 파일럿 본실험용 workflow는 별도 파일로 두는 것이 좋다.

#### C. `EnvBench/conf/base.yaml`은 workflow orchestration에 필요한 hook를 이미 일부 제공한다

현재 상태:
- `traj_repo_id`
- `run_name`
- `EVAL_TOOL`
- local data source
- evaluation input column mapping

이 이미 들어가 있다.

판단:
- workflow는 이 hook를 활용하면 되고, EnvBench config를 크게 뜯을 필요는 없다.
- 섹션 5의 핵심은 “workflow에서 어떤 env/override를 넣을지 고정”하는 것이다.

### 섹션 5 진행 상황

#### Step 1: 새 workflow 파일 생성 ✅ 완료

생성 파일:
- [.github/workflows/envbench-python-native.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-native.yml)

기존 heuristic workflow (`envbench-python-matrix.yml`)와 완전히 분리된 새 파일로 생성.

workflow 구조:
- **trigger**: `workflow_dispatch` only (파일럿 단계에서 push 시 자동 30 job 방지)
- **input**: `hf_traj_repo_id` (HuggingFace trajectory dataset repo, 기본값 `JetBrains-Research/EnvBench-trajectories`)

workflow-level env (pinned versions):
- `EXECUTION_MODE=native`
- `EVAL_TOOL=native`
- `PYTHON_VERSION=3.12.8`
- `NODE_VERSION=20.18.0`
- `UV_VERSION=0.6.14`
- `PYRIGHT_VERSION=1.1.390`

#### Step 2: experiment job matrix 구성 ✅ 완료

matrix 설계:
- OS 차원: `{runs_on, target_os}` 3개
  - `ubuntu-22.04` / `linux`
  - `macos-14` / `macos`
  - `windows-2022` / `windows`
- repo 차원: `{repository, revision, safe_name}` 10개 (source: `selected_repos.jsonl`)
- cross product: 3 × 10 = 30 jobs
- `fail-fast: false`
- `timeout-minutes: 60`

job-level env:
- `TARGET_OS=${{ matrix.os.target_os }}`
- `RUN_NAME=gha-python-bash-{target_os}-{safe_name}-{run_id}-{run_attempt}`
- `HF_TRAJ_REPO_ID=${{ inputs.hf_traj_repo_id }}`

#### Step 3: experiment job steps 구현 ✅ 완료

10 steps 구현:

1. **Checkout** — `actions/checkout@v4`
2. **Setup Python** — `actions/setup-python@v5`, pinned `PYTHON_VERSION`
3. **Setup Node** — `actions/setup-node@v4`, pinned `NODE_VERSION`
4. **Install uv** — `astral-sh/setup-uv@v5`, pinned `UV_VERSION`
5. **Environment snapshot** — `$GITHUB_WORKSPACE/artifacts/environment_snapshot.txt`에 runner.os, runner.arch, ImageOS, ImageVersion, 모든 tool version, 실험 설정 기록
6. **Install EnvBench dependencies** — `uv venv` + `uv sync` + `pip install pyright==$PYRIGHT_VERSION`
7. **Prepare single-repo manifest** — matrix row에서 `{repository, revision}` 추출, `local_manifest.jsonl` 생성
8. **Run EnvBench pipeline** — `python envbench.py --config-name python-bash` + Hydra overrides
9. **Collect results** — trajectories, scripts.jsonl, results.jsonl, evaluation JSON을 `artifacts/` 디렉토리로 수집. `job_metadata.json` 작성
10. **Upload artifacts** — `actions/upload-artifact@v4`, `if: always()`, retention 30일

Hydra overrides (Step 8):
```
run_name=${RUN_NAME}
tag=gha-python-bash
use_wandb=false
fancy_output=false
inference_workers=1
eval_workers=1
data_path=${GITHUB_WORKSPACE}/data
tmp_dir=${GITHUB_WORKSPACE}/tmp
traj_repo_id=${HF_TRAJ_REPO_ID}
inference.data_source.type=local
inference.data_source.local.path=${GITHUB_WORKSPACE}/tmp/local_manifest.jsonl
inference.log_trajectory=true
inference.rewrite_trajectories=true
inference.max_concurrent=1
evaluation.eval_tool=native
evaluation.operation.pool_config.max_workers=1
```

secrets:
- `OPENAI_API_KEY` — LLM API key (inference 단계에서 사용)
- `HF_TOKEN` — HuggingFace write 권한 (trajectory/scripts/results 업로드)

cross-platform 대응:
- 모든 step에서 `shell: bash` 통일 (Windows에서 Git Bash 사용)
- venv 활성화: `source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate`
- 모든 경로를 `$GITHUB_WORKSPACE` 기준 절대 경로로 지정 (Hydra CWD 변경 영향 차단)

#### Step 4: aggregate job 구현 ✅ 완료

생성 파일:
- [scripts/aggregate_results.py](/Users/corkang/Desktop/Research/EnvBench-Multi/scripts/aggregate_results.py)

aggregate job 구조:
- `needs: experiment`, `if: always()`, `runs-on: ubuntu-22.04`, `timeout-minutes: 10`
- `actions/download-artifact@v4`로 `result-*` 패턴의 모든 per-job artifact 다운로드
- `scripts/aggregate_results.py` 실행
- `actions/upload-artifact@v4`로 `aggregated-results` 업로드 (retention 90일)

aggregate script 기능:
- 각 artifact 디렉토리에서 `job_metadata.json` + `results.jsonl` (또는 evaluation JSON) 로드
- `pass@1 = (exit_code == 0) and (issues_count == 0)` 계산
- 출력 파일:
  - `results_all.jsonl` — 전체 결과 (1행 = 1 repo × 1 OS)
  - `results_all.csv` — 동일 내용 CSV
  - `summary.md` — repo × OS matrix 테이블 + PASS/FAIL/ERROR 집계

#### Step 5: 검증 ✅ 완료

- YAML 구문 검증 통과 (PyYAML `safe_load`)
- matrix 구조 확인: 10 repos × 3 OS = 30 jobs
- experiment job 10 steps 확인
- aggregate script Python syntax 검증 통과
- cross-platform 경로/shell 호환성 점검

#### 섹션 5 최종 판정

**섹션 5 구현 완료.**

핵심 완료 조건:
1. ✅ heuristic workflow와 분리된 새 workflow 파일 생성
2. ✅ `selected_repos.jsonl` 기반 10 repo × 3 OS matrix
3. ✅ inference → processing → evaluation 전체 파이프라인 호출 (envbench.py 단일 호출)
4. ✅ pinned Python/Node/uv/pyright versions
5. ✅ `workflow_dispatch` trigger (auto-run 방지)
6. ✅ `fail-fast: false` (부분 실패가 전체를 중단시키지 않음)
7. ✅ environment snapshot 기록 (ImageOS, ImageVersion, tool versions)
8. ✅ per-job artifact upload (`if: always()`)
9. ✅ aggregate job: `results_all.jsonl`, `results_all.csv`, `summary.md` 생성
10. ✅ cross-platform `shell: bash` 통일, 절대 경로 사용

#### Step 6: 섹션 5 후속 수정 (2차) ✅ 완료

지적 사항 4건에 대한 수정:

##### 1. [High] 기존 heuristic workflow 비활성화 ✅

파일: [envbench-python-matrix.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-matrix.yml)

변경 내용:
- `push` trigger 제거 → `workflow_dispatch`만 남김 (main push 시 자동 실행 방지)
- workflow name에 `[DEPRECATED]` prefix 추가
- 파일 상단에 deprecation 안내 주석 추가 (active workflow는 `envbench-python-native.yml`임을 명시)
- 존재하지 않는 스크립트를 호출하는 step 4개 주석 처리:
  - `run_eval_linux.sh`, `run_eval_macos.sh`, `run_eval_windows.ps1`, `collect_result.py`
- setup step과 checkout step은 유지 (참고용)

##### 2. [Medium] `python_build.sh` line 41 — 이미 수정 완료 ✅

현재 상태 확인:
- line 41은 이미 `python -c "`로 되어 있음 (이전 세션에서 수정 완료)
- `python3` 호출이 파일 내에 0건임을 확인

##### 3. [Medium] manifest-workflow matrix drift 방지 ✅

파일: [envbench-python-native.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-native.yml)

변경 내용:
- experiment job에 "Validate matrix entry against selected_repos.jsonl" step 추가 (Step 4b)
- 각 job 실행 시 matrix의 `{repository, revision}` 쌍이 `manifests/selected_repos.jsonl`에 존재하는지 검증
- 불일치 시 명시적 에러 메시지와 함께 job 실패
- manifest를 업데이트했으나 workflow matrix를 동기화하지 않은 경우 즉시 감지 가능

##### 4. [Medium] 재현성 고정 — action SHA pin + aggregate Python 버전 ✅

파일: [envbench-python-native.yml](/Users/corkang/Desktop/Research/EnvBench-Multi/.github/workflows/envbench-python-native.yml)

변경 내용:
- 모든 GitHub Actions를 tag(`@v4`, `@v5`)에서 commit SHA로 pin 변경:
  - `actions/checkout` → `@11bd71901bbe5b1630ceea73d27597364c9af683` (v4)
  - `actions/setup-python` → `@a26af69be951a213d495a4c3e4e4022e16d87065` (v5)
  - `actions/setup-node` → `@49933ea5288caeca8642d1e84afbd3f7d6820020` (v4)
  - `astral-sh/setup-uv` → `@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86` (v5)
  - `actions/upload-artifact` → `@ea165f8d65b6e75b540449e92b4886f43607fa02` (v4)
  - `actions/download-artifact` → `@d3f86a106a0bac45b974a628896c90dbdf5c8093` (v4)
- experiment job과 aggregate job 양쪽 모두 SHA pin 적용
- aggregate job Python 버전: `"3.12"` → `"3.12.8"` (experiment job과 동일)

검증:
- YAML 구문 유효성 확인
- 모든 `uses:` 필드에 40-char hex SHA 포함 확인
- aggregate Python version `3.12.8` 확인

남은 후속 작업:
- ⏳ 1-repo smoke run으로 workflow 실행 확인 (secrets 설정 필요)
- ⏳ prompt toolchain 설명을 workflow가 보장하는 도구와 최종 정합 (현재 이미 근사치)

## 6. Hugging Face 연동 규칙

### 목표

- EnvBench의 기존 `trajectories -> scripts.jsonl -> results.jsonl` 구조를 유지하면서, GitHub Actions에서도 실험 산출물을 재현 가능하게 저장한다.

### 고정할 사실

- 현재 `envbench` pipeline은 `scripts.jsonl`과 `results.jsonl` 업로드에 Hugging Face를 사용한다.
- end-to-end GitHub Actions 실험에는 HF write 권한이 필요하다.

### 요구사항

- HF repo 구조는 run 단위로 구분되어야 한다.
- `run_name` 기준으로 아래가 정리되어야 한다.
  - `trajectories/`
  - `scripts.jsonl`
  - `results.jsonl`
- 실패한 job도 가능한 범위의 partial artifact를 local Actions artifact로 남겨야 한다.
- HF 업로드 실패와 workflow 실행 실패를 구분해서 로그에 남겨야 한다.

### 문서에 적을 제약

- GitHub Actions에서 외부 업로드가 필요한 만큼 `HF_TOKEN`이 없으면 full pipeline은 완료되지 않는다.
- 재현성 확보를 위해 HF만 믿지 말고 Actions artifacts도 보조 저장소로 사용한다.

### 산출물

- HF output naming convention
- Actions artifact fallback 규칙

### 검증 기준

- 1개 smoke run에서 HF와 artifact 양쪽에 결과가 남아야 한다.
- run_name만으로 해당 실험의 trajectory, script, result를 추적할 수 있어야 한다.

## 7. 검증, 재현성, failure analysis

### 목표

- 코드 수정 이후 실험이 실제로 reproducible하고 해석 가능하다는 것을 보장한다.

### 검증 단계

#### A. 문서 검증

- 각 단계가 아래 형식을 따르는지 확인한다.
  - 목표
  - 수정 파일
  - 요구사항
  - 제약
  - 산출물
  - 검증 기준
- 장문의 코드 블록이 핵심이 되지 않도록 유지한다.
- 예시는 필요한 경우 인터페이스 수준으로만 남긴다.

#### B. 기술 smoke test

- native executor import path가 실제로 맞는지 확인
- native evaluation path가 실제로 선택되는지 확인
- Windows에서도 `bash` 기준 orchestration이 가능한지 확인

#### C. 실험 smoke test

- 1개 repo x 3 OS smoke run
- trajectories 생성 확인
- `scripts.jsonl` 생성 확인
- `results.jsonl` 생성 확인

#### D. 파일럿 본실험

- 10개 repo x 3 OS full pilot
- HF 산출물과 Actions artifact가 모두 남는지 확인
- 집계 결과에서 아래가 일관되게 계산되는지 확인
  - `pass@1`
  - `issues_count`
  - bootstrap exit

### 결과를 읽는 규칙

결과는 최소 3개 층위로 나눠서 본다.

1. bootstrap script generation 성공 여부
2. bootstrap 실행 성공 여부
3. EnvBench metric 기준 `pass@1`

### failure taxonomy 초안

- package manager mismatch
- missing system headers / build toolchain
- Python version mismatch
- path / shell incompatibility
- permission / sudo constraints
- architecture-specific dependency issue

### Linux 대비 저하를 읽는 규칙

- Linux 성공, macOS/Windows 실패면 OS-specific dependency 가능성을 먼저 본다.
- Linux와 macOS 모두 실패면 repo 자체 난이도 또는 agent planning failure 가능성을 먼저 본다.
- macOS만 실패하면 `brew` 또는 arm64 issue를 우선 본다.
- Windows만 실패하면 shell/path/permission/toolchain mismatch를 우선 본다.

### 산출물

- smoke run checklist
- full pilot checklist
- taxonomy template

### 검증 기준

- 로그만 읽어도 실패를 taxonomy에 배치할 수 있어야 한다.
- 결과 해석이 `metric 변화`와 `execution substrate 변화`를 혼동하지 않아야 한다.

## 8. 단계별 구현 요청에 바로 쓸 수 있는 Prompting 포맷

각 단계는 아래 형식으로 에이전트에게 맡기는 것을 권장한다.

### 포맷

```text
목표:
이번 단계에서 해결할 문제를 한 문단으로 설명

수정 대상 파일:
- 파일 1
- 파일 2

유지해야 할 것:
- 기존 인터페이스
- 기존 metric semantics

바꿔야 할 것:
- Docker dependency 제거
- native mode 분기 추가

제약:
- Windows에서도 가능하면 bash orchestration 유지
- pyright metric 정의 변경 금지

완료 조건:
- smoke test 1개 통과
- artifact/로그 확인 가능
```

이 문서의 각 단계는 위 포맷으로 쪼개서 바로 구현 요청에 사용할 수 있어야 한다.

## 9. 최종 체크리스트

- [ ] 실험 목표가 portability가 아니라 OS별 rerun 실험으로 고정되어 있다.
- [ ] manifest source가 `selected_repos.jsonl`로 고정되어 있다.
- [ ] inference 수정 대상 파일과 요구사항이 명확하다.
- [ ] evaluation 수정 대상 파일과 요구사항이 명확하다.
- [ ] workflow의 pinned versions가 명확하다.
- [ ] HF write requirement가 명확하다.
- [ ] `pass@1` 정의와 Python metric semantics가 보존된다.
- [ ] smoke test와 full pilot 검증 기준이 적혀 있다.
- [ ] failure taxonomy 초안이 적혀 있다.

---

이 가이드는 구현 자체보다 구현 명세를 위한 문서다. 다음 단계에서는 이 문서를 기준으로 inference 수정, evaluation 수정, workflow 수정, smoke test 순으로 각각 별도 작업 지시를 내리는 방식으로 진행한다.

---

## 10. Windows 디버깅 진행 로그

Section 3-5 구현 완료 후, `windows-2022` runner에서 workflow를 실행하며 발견된 에러를 순차적으로 수정한 기록이다.

### Bug #1: GitPython `--config` 차단 ✅ 수정 완료

- **증상**: `git.exc.UnsafeOptionError: --config is not allowed`
- **원인**: GitPython 3.1.41+에서 `--config`를 unsafe option으로 차단. Windows에서 `core.longpaths=true` 설정을 위해 `clone_from(multi_options=["--config", "core.longpaths=true"])` 사용 시 발생.
- **수정 파일**: `EnvBench/env_setup_utils/repo_downloader.py`
- **수정 내용**: `clone_from()`에 `allow_unsafe_options=True` 추가
- **커밋**: `0ce852f`

### Bug #2: Trajectory visualization HF 다운로드 실패 ✅ 수정 완료

- **증상**: `RuntimeError: No .jsonl files found matching datasets/corkang/EnvBench-multi-traj/.../trajectories/*.jsonl`
- **원인**: inference 후 `generate_trajectories_html_from_hf()`가 HuggingFace에서 trajectory를 다운로드하려 하지만, trajectory가 아직 HF에 업로드되지 않은 상태.
- **수정 파일**: `EnvBench/envbench.py`
- **수정 내용**: trajectory/scripts/evaluation visualization을 모두 `try-except`로 감싸서 실패해도 파이프라인 계속 진행
- **커밋**: `713f371`, `8fa0f1f`

### Bug #3: Evaluation이 HF에서 `scripts.jsonl` 다운로드 시 404 ✅ 수정 완료

- **증상**: `EntryNotFoundError: 404 Client Error` for `scripts.jsonl`
- **원인**: 원본 파이프라인은 processing→HF 업로드→evaluation이 HF에서 다운로드하는 흐름. Native CI에서는 HF 업로드가 실패/지연되어 evaluation이 `scripts.jsonl`을 찾지 못함.
- **수정 파일**: `EnvBench/env_setup_utils/process_trajectories_to_scripts.py`, `EnvBench/envbench.py`
- **수정 내용**:
  - `process_trajectories_to_scripts()`에 `local_output_path` 파라미터 추가하여 HF 업로드 전 로컬 복사본 저장
  - `envbench.py`에서 evaluation 전 로컬 `scripts.jsonl`이 있으면 `evaluation.input.mode=local`로 자동 전환
- **커밋**: `8fa0f1f`

### Bug #4: `python_build.sh` 파일 읽기 시 인코딩 에러 ✅ 수정 완료

- **증상**: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 95`
- **원인**: Windows 기본 인코딩이 `cp1252`인데, `python_build.sh`에 UTF-8 문자가 포함되어 `open()` 시 디코딩 실패.
- **수정 파일**: `EnvBench/evaluation/main.py`
- **수정 내용**: `read_script()` 함수에서 `open(script_path, "r", encoding="utf-8")` 명시
- **커밋**: 미커밋 (현재 워킹 트리)

### Bug #5: `run_native()` 내 파일 쓰기 인코딩 에러 ✅ 수정 완료

- **증상**: `UnicodeEncodeError: 'charmap' codec can't encode characters in position 77-78`
- **원인**: Bug #4와 동일 계열. `run_native()`에서 bootstrap/build 스크립트를 파일로 쓰거나 결과 JSON을 저장할 때 `encoding` 미지정으로 Windows `cp1252`가 사용됨.
- **수정 파일**: `EnvBench/evaluation/main.py`
- **수정 내용**: `run_native()` 내 모든 `open()` 호출에 `encoding="utf-8"` 추가 (line 310, 319, 339, 371, 397)
- **커밋**: 미커밋 (Bug #4와 함께 워킹 트리)
- **발견 경로**: `gh run view 24137045499 --log`로 직접 확인. Bug #4의 read 에러(`UnicodeDecodeError`)와 달리 write 에러(`UnicodeEncodeError`)

### 현재 상태

- **Step 1 (Inference)**: 정상 완료 (agent가 max iterations까지 실행)
- **Step 2 (Processing)**: 정상 완료 (로컬 scripts.jsonl 생성 확인)
- **Step 3 (Evaluation)**: Bug #4, #5 수정 후 재실행 필요
- **다음 확인 사항**: evaluation의 `run_native()` → `python_build.sh` 실행 → pyright metric 수집이 Windows에서 정상 동작하는지 확인
