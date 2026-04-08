# EnvBench-Multi: Cross-Platform Environment Setup Research

## 연구 개요

**Automated Environment Setup** 분야의 연구 프로젝트.

기존 EnvBench / Repo2Run 등 선행 연구는 Docker 기반 Linux 환경에서만 평가한다. 이 레포는 Linux 전제로 설계된 EnvBench `bash agent`가 **macOS·Windows native 환경에서도 동작하는지** 실험하고, 실패 유형을 분석해 **ASE NIER Track** 제출을 목표로 한다.

## 논문: EnvBench (ICLR 2025 Workshop on DL4Code)

**"EnvBench: A Benchmark for Automated Environment Setup"**
Aleksandra Eliseeva, Alexander Kovrigin, Ilia Kholkin, Egor Bogomolov, Yaroslav Zharov
arXiv: 2503.14443

### 핵심 내용

- **데이터셋**: 994개 repo (Python 329개 + JVM 665개), HuggingFace에 공개
  - 단순 `pip install`로 해결 가능한 repo는 제외하고, 실질적 설정 복잡성이 있는 repo만 선별
- **Agent 구조**: LangGraph 기반 ReAct agent. Docker container 내부에서 bash tool 실행 → trajectory 생성
- **평가 지표**:
  - Python: `pyright`로 `reportMissingImports` 개수 집계. `pass@1 = (exit_code == 0) and (issues_count == 0)`
  - JVM: 컴파일 성공 여부
- **베이스라인 성능**: Python 6.69%, JVM 29.47% (GPT-4o 기준)
- **파이프라인**: Inference → Processing (trajectory → scripts.jsonl) → Evaluation

## 레포지토리 구조

```
EnvBench-Multi/
├── EnvBench/                    # EnvBench 원본 코드 (git clone)
│   ├── envbench.py              # 메인 오케스트레이터 (Hydra + WandB)
│   ├── conf/                    # Hydra 설정 파일들 (python-bash.yaml, jvm-bash.yaml 등)
│   ├── inference/               # LLM agent 실행 모듈
│   │   ├── main.py              # inference 진입점 (async, per-datapoint)
│   │   ├── native_shell_executer.py  # native OS executor 초안 (이름 오타 있음)
│   │   ├── configs/
│   │   │   ├── toolkit_config.py     # Docker/native executor 분기 선택
│   │   │   ├── run_inference_config.py
│   │   │   └── docker_config.py      # DockerConfig (native mode에서도 파라미터 재사용)
│   │   └── src/
│   │       ├── agents/
│   │       │   └── python/
│   │       │       ├── agent.py      # EnvSetupPythonAgent (ReAct)
│   │       │       └── prompts.py    # OS별 system prompt 분기 (EXECUTION_MODE, TARGET_OS)
│   │       ├── toolkits/
│   │       │   ├── base.py           # BaseEnvSetupToolkit (현재 Docker 전제 표현 있음)
│   │       │   └── bash_terminal.py  # bash tool 정의
│   │       ├── async_bash_executor.py   # Docker 기반 executor (원본)
│   │       └── env_setup_runner.py      # trajectory 파일 생성·저장
│   ├── evaluation/              # 평가 모듈
│   │   ├── main.py              # run_opensource(Docker) + run_native() 초안
│   │   └── scripts/
│   │       └── python_build.sh  # pyright metric 수집 (jq 의존성 문제 있음)
│   └── env_setup_utils/         # 공통 유틸 (analysis, data_sources, markdown)
├── EnvBench-trajectories/       # HuggingFace에서 다운받은 trajectory 데이터
├── .github/
│   └── workflows/
│       ├── envbench-python-native.yml   # 본 실험 workflow (EnvBench agent, 10 repo × 3 OS)
│       └── envbench-python-matrix.yml   # 참고용: heuristic setup smoke test (EnvBench agent 아님)
├── manifests/
│   ├── selected_repos.jsonl     # 파일럿 10개 repo (canonical source of truth)
│   └── python10.json            # 참고용 목록 (직접 사용 금지)
└── scripts/
    ├── aggregate_results.py     # aggregate job용 결과 병합 스크립트
    └── run_setup_{linux,macos,windows} 등 helper scripts
```

## 실험 설계

### 연구 질문

1. Linux/Docker 전제로 설계된 EnvBench `bash agent`가 `ubuntu-22.04`, `macos-14`, `windows-2022` native 환경에서도 environment setup을 수행할 수 있는가?
2. OS가 바뀌었을 때 setup 실패는 어떤 유형으로 나타나는가?

### 파일럿 범위

- **대상 언어**: Python
- **대상 OS**: `ubuntu-22.04`, `macos-14` (Apple Silicon arm64), `windows-2022`
- **대상 repo**: `manifests/selected_repos.jsonl` 10개 repo
- **평가 지표**: EnvBench 기존 Python metric 유지 (변경 금지)

### Target 파이프라인

```
원본:  EnvBench agent → Docker container bash 실행 → trajectory → eval (Docker)
목표:  EnvBench agent → GitHub Actions native OS bash 실행 → trajectory → eval (native)
```

metric 정의(pass@1, reportMissingImports)는 **변경하지 않고** execution substrate만 native로 교체한다.

### 파일럿 10개 repo (source: selected_repos.jsonl)

1. `pytest-dev/pytest-xdist` @ `c7b4f611...`
2. `facebookresearch/hydra` @ `2e682d84...`
3. `jageo/lobsterpy` @ `55d8d2e1...`
4. `mad-lab-fau/biopsykit` @ `2ad99fba...`
5. `skrub-data/skrub` @ `0d69d971...`
6. `vacanza/python-holidays` @ `472e89d8...`
7. `mov-cli/mov-cli` @ `32f8af4f...`
8. `cookiecutter/cookiecutter` @ `f49dcf97...`
9. `robbievanleeuwen/section-properties` @ `0a41ea14...`
10. `dagshub/client` @ `f8d89c53...`

> 값이 다를 경우 항상 `selected_repos.jsonl`이 우선이다.

## 현재 코드 상태

### Section 3: Inference native화 ✅ 완료

| 파일 | 변경 내용 |
|------|-----------|
| `inference/src/native_shell_executor.py` | **신규**. Persistent bash session (stdin/stdout stream), end marker + exit code 프로토콜, `env_vars` 주입, `repository_workdir` 분기, `clear_repo` cleanup, `_find_bash()` OS별 경로, timeout 시 session restart + command replay |
| `inference/native_shell_executer.py` | **삭제** (오타 파일) |
| `inference/configs/toolkit_config.py` | import path 정상 (파일 이동으로 자동 해결) |
| `inference/src/toolkits/base.py` | `bash_executor` 타입을 `Union[AsyncBashExecutor, NativeShellExecutor]`로 변경 |
| `inference/src/toolkits/bash_terminal.py` | docstring Docker → execution environment |
| `inference/src/agents/python/prompts.py` | `get_system_prompt()` 함수로 변경, `TARGET_OS` validation, OS별 설명 정밀화 |
| `inference/main.py` | logging 문구 일반화 ("container" → "execution environment") |

### Section 4: Evaluation native화 ✅ 완료

| 파일 | 변경 내용 |
|------|-----------|
| `evaluation/main.py` | `run_native()` 공식 경로 승격. `_find_bash()` 추가, 절대 경로 실행, 실패 시에도 결과 JSON 보장, Docker import lazy화 |
| `evaluation/scripts/python_build.sh` | `jq` 전량 제거 → Python inline JSON 집계. `chmod -R 777 .` 제거. bootstrap `|| true` 추가 |
| `evaluation/conf/config.yaml` | `eval_tool: ${oc.env:EVAL_TOOL,opensource}` 환경변수 주입 패턴 통일 |

### Section 5: GitHub Actions workflow ✅ 완료

| 파일 | 변경 내용 |
|------|-----------|
| `.github/workflows/envbench-python-native.yml` | **신규**. 10 repo × 3 OS matrix workflow. `envbench.py` 단일 호출로 inference→processing→evaluation 실행. pinned Python/Node/uv/pyright, environment snapshot, aggregate job |
| `scripts/aggregate_results.py` | **신규**. per-job artifact에서 `results_all.jsonl`, `results_all.csv`, `summary.md` 생성 |

## 수정 작업 우선순위

`envbench_crossplatform_guide.md`에 상세 명세가 있다. 요약:

1. ~~**Section 3 (Inference native화)**~~ ✅ 완료

2. ~~**Section 4 (Evaluation native화)**~~ ✅ 완료

3. ~~**Section 5 (GitHub Actions workflow)**~~ ✅ 완료

## 핵심 불변 조건 (변경 금지)

- Python metric 정의: `pass@1 = (exit_code == 0) and (issues_count == 0)`
- `reportMissingImports` 집계 방식
- trajectory → scripts.jsonl → results.jsonl 흐름
- manifest schema: `repository`, `revision` 필드명
- trajectory 파일명 규칙: `<repository>@<revision>.jsonl`
- canonical manifest source: `manifests/selected_repos.jsonl`

## 환경 변수

inference 실행 시 주입:
- `EXECUTION_MODE`: `docker` | `native`
- `TARGET_OS`: `linux` | `macos` | `windows`
- `OPENAI_API_KEY` 또는 사용 LLM provider 키

## Windows 디버깅 사이클 (자동 실행 프로세스)

workflow 실패를 발견하고 수정하는 반복 사이클. 아래 순서를 자동으로 실행한다.

### 1. 수정 커밋 & push

```bash
# 수정한 파일만 stage (절대 git add -A 하지 않는다)
git add <수정파일1> <수정파일2>
git commit -m "fix: <간결한 설명>"
git push origin test
```

### 2. workflow 트리거

```bash
gh workflow run "EnvBench Python Native" --ref test
```

### 3. 완료 대기 (~7분)

```bash
# 가장 최근 run ID 취득 후 watch (완료까지 blocking)
RUN_ID=$(gh run list --workflow="EnvBench Python Native" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch $RUN_ID
```

### 4. 결과 확인

```bash
# job별 성공/실패 확인
gh run view $RUN_ID --json jobs --jq '.jobs[] | {name: .name, conclusion: .conclusion}'

# 실패 시 에러 로그 추출
gh run view $RUN_ID --log 2>&1 | grep -B 3 -A 5 -i "error\|traceback\|exception" | head -100
```

### 5. 실패 시 수정

- 에러 로그에서 원인 파악
- 해당 파일 수정
- `envbench_crossplatform_guide.md`의 "Section 10. Windows 디버깅 진행 로그"에 Bug 항목 추가
- 1번으로 돌아가 반복

### 6. 성공 시

- `envbench_crossplatform_guide.md`의 "현재 상태" 섹션 업데이트
- 다음 OS (linux, macos) 활성화 검토

## 관련 문서

- 상세 설계 명세: [envbench_crossplatform_guide.md](envbench_crossplatform_guide.md)
- EnvBench 원본: [EnvBench/README.md](EnvBench/README.md)
- HuggingFace 데이터셋: `JetBrains-Research/EnvBench`
- HuggingFace trajectories: `JetBrains-Research/EnvBench-trajectories`