import asyncio
from collections import deque
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

import hydra
from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf, open_dict
from rich import box
from rich.align import Align
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table

# Configure rich traceback for better error display
from rich.traceback import install
import wandb

from env_setup_utils.analysis.scripts_viewer import generate_scripts_html_from_hf
from env_setup_utils.analysis.traj_viewer import generate_trajectories_html_from_hf
from env_setup_utils.analysis.view_logs import generate_logs_html_from_hf
from env_setup_utils.process_trajectories_to_scripts import process_trajectories_to_scripts
from evaluation.main import main as run_evaluation

# Import inference and evaluation modules
from inference.main import main as run_inference

install(show_locals=True, width=120, word_wrap=True)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler(rich_tracebacks=True, markup=True)])
logger = logging.getLogger("pipeline")

STEP_STYLES = {"inference": "blue", "processing": "green", "evaluation": "yellow"}

def _supports_unicode_output() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return encoding.startswith("utf")


UNICODE_OUTPUT = _supports_unicode_output()
PANEL_BOX = box.ROUNDED if UNICODE_OUTPUT else box.ASCII
HEADER_BOX = box.DOUBLE if UNICODE_OUTPUT else box.ASCII


STEP_EMOJIS = {
    "inference": "🤖" if UNICODE_OUTPUT else "[I]",
    "processing": "🔄" if UNICODE_OUTPUT else "[P]",
    "evaluation": "📊" if UNICODE_OUTPUT else "[E]",
}

# Global fancy output setting
FANCY_OUTPUT = False


@dataclass
class PipelineConfig:
    tmp_dir: str
    file_name: str
    run_name: str
    tag: str
    wandb_project: Optional[str] = "envsetup-oss"
    use_wandb: bool = True
    skip_inference: bool = False
    skip_processing: bool = False
    skip_evaluation: bool = False
    data_path: str = str(Path.home() / "data_path")
    fancy_output: bool = True


cs = ConfigStore.instance()
cs.store(name="pipeline_config", node=PipelineConfig)


def ui_text(unicode_text: str, ascii_text: str) -> str:
    return unicode_text if UNICODE_OUTPUT else ascii_text


def create_fancy_header(text: str, style: str = "magenta") -> Panel:
    """Create a fancy header with a double-line box and centered text"""
    return Panel(
        Align.center(text),
        style=style,
        box=HEADER_BOX,
        padding=(1, 2),
        title=ui_text("✨ Environment Setup Pipeline ✨", "Environment Setup Pipeline"),
        title_align="center",
    )


def create_step_header(step: str, number: int, style: str) -> Panel:
    """Create a fancy step header with emoji and rounded box"""
    emoji = STEP_EMOJIS.get(step.lower(), "[*]")
    return Panel(
        f"{emoji} Step {number}: {step}",
        style=style,
        box=PANEL_BOX,
        padding=(1, 2),
        title=ui_text("⚡️", "*"),
        title_align="right",
    )


def stream_subprocess_output(
    process: subprocess.Popen, description: str, progress: Progress, style: str = "blue"
) -> None:
    """Stream subprocess output in a nicely formatted way"""
    global FANCY_OUTPUT
    output_queue = queue.Queue()
    output_lines = deque(maxlen=15)
    task_id = progress.add_task(description, total=None)
    last_update = time.time()
    update_interval = 0.5  # Update every 0.5 seconds

    def reader(pipe, queue):
        try:
            with pipe:
                for line in iter(pipe.readline, ""):
                    queue.put(line.strip())
        finally:
            queue.put(None)

    stdout_thread = threading.Thread(target=reader, args=(process.stdout, output_queue))
    stderr_thread = threading.Thread(target=reader, args=(process.stderr, output_queue))
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()

    # Create initial panel if using fancy output
    if progress.console.is_interactive and FANCY_OUTPUT:
        panel = Panel(
            "",
            title=f"[bold]{description}[/bold]",
            border_style=style,
            box=PANEL_BOX,
            title_align="center",
            padding=(1, 2),
            subtitle=ui_text("🔄 Live Output", "Live Output"),
            subtitle_align="right",
        )
        progress.console.print(panel)

    while stdout_thread.is_alive() or stderr_thread.is_alive() or not output_queue.empty():
        try:
            line = output_queue.get(timeout=0.1)
            if line is None:
                continue
            output_lines.append(line)

            # Update output based on mode
            current_time = time.time()
            if current_time - last_update >= update_interval:
                if progress.console.is_interactive and FANCY_OUTPUT:
                    progress.console.print(
                        Panel(
                            "\n".join(output_lines),
                            title=f"[bold]{description}[/bold]",
                            border_style=style,
                            box=PANEL_BOX,
                            title_align="center",
                            padding=(1, 2),
                            subtitle=ui_text("🔄 Live Output", "Live Output"),
                            subtitle_align="right",
                        )
                    )
                else:
                    # Simple output mode
                    for line in output_lines:
                        progress.console.print(escape(line))
                    output_lines.clear()
                last_update = current_time

        except queue.Empty:
            continue

    # Print final state
    if output_lines:
        if progress.console.is_interactive and FANCY_OUTPUT:
            progress.console.print(
                Panel(
                    "\n".join(output_lines),
                    title=f"[bold]{description}[/bold]",
                    border_style=style,
                    box=PANEL_BOX,
                    title_align="center",
                    padding=(1, 2),
                    subtitle=ui_text("✅ Complete", "Complete"),
                    subtitle_align="right",
                )
            )
        else:
            # Simple output mode
            for line in output_lines:
                progress.console.print(escape(line))

    process.wait()
    progress.remove_task(task_id)


def run_command_with_progress(
    func: Callable,
    args: tuple,
    description: str,
    progress: Progress,
    style: str = "blue",
    data_path: str | None = None,
    count_pattern: str | None = None,
) -> None:
    """Run a coroutine function with progress tracking"""
    progress.console.print(
        Panel(
            f"[bold]Running function:[/bold]\n{func.__name__}: {description}",
            style=style,
            box=PANEL_BOX,
            padding=(1, 2),
        )
    )
    if data_path:
        os.environ["DATA_ROOT"] = data_path

    task_id = progress.add_task(f"[{style}]{description}", total=None)

    try:
        res = func(*args)
        if asyncio.iscoroutine(res):
            asyncio.run(res)
    except Exception as e:
        progress.remove_task(task_id)
        raise e

    progress.remove_task(task_id)


def create_artifact_table(title: str, repo_id: str, artifacts: list[tuple[str, str]], style: str = "blue") -> Table:
    """Create a table showing HuggingFace artifacts"""
    table = Table(title=title, box=PANEL_BOX, style=style, title_style=f"bold {style}")
    table.add_column("Artifact", style="bold")
    table.add_column("Location on HuggingFace")

    for name, path in artifacts:
        table.add_row(name, f"{repo_id}/{path}")

    return table


def create_summary_table(artifacts: list[tuple[str, str, str]]) -> Table:
    """Create a summary table showing all HuggingFace artifacts"""
    table = Table(
        title=ui_text("🗂️  Pipeline Artifacts Summary", "Pipeline Artifacts Summary"),
        box=PANEL_BOX,
        title_style="bold magenta",
        caption=ui_text("All artifacts have been uploaded to HuggingFace 🤗", "All artifacts have been uploaded to HuggingFace"),
        caption_style="dim",
    )
    table.add_column("Stage", style="bold")
    table.add_column("Repository", style="cyan")
    table.add_column("Path", style="green")

    for stage, repo, path in artifacts:
        table.add_row(stage, repo, path)

    return table


def resolve_cfg(cfg):
    return OmegaConf.create(OmegaConf.to_object(cfg))  # hack


def create_separator_rule(style: str = "bright_black") -> Rule:
    return Rule(style=style, characters=ui_text("─", "-"))


@hydra.main(version_base=None, config_path="conf", config_name="base")
def main(cfg: DictConfig) -> None:
    console = Console(emoji=UNICODE_OUTPUT)
    # Track all artifacts for final summary
    artifacts = []

    # Print fancy header
    console.print(create_fancy_header("[bold]Environment Setup Pipeline[/bold]"))

    # Print configuration panel
    config_text = [
        "[bold white]Configuration Details[/bold white]",
        ui_text("─", "-") * 50,
        f"[bold]{ui_text('🏷️  Run name:', 'Run name:')}[/bold] {cfg.run_name}",
        f"[bold]{ui_text('💾 Data path:', 'Data path:')}[/bold] {cfg.data_path}",
        f"[bold]{ui_text('🔄 Active Steps:', 'Active Steps:')}[/bold] "
        + " ".join(
            [
                f"[{STEP_STYLES[step.lower()]}]{step}[/{STEP_STYLES[step.lower()]}]"
                for step, enabled in {
                    "Inference": not cfg.skip_inference,
                    "Processing": not cfg.skip_processing,
                    "Evaluation": not cfg.skip_evaluation,
                }.items()
                if enabled
            ]
        ),
        f"[bold]{ui_text('📊 Wandb:', 'Wandb:')}[/bold] {'[green]enabled[/green]' if cfg.use_wandb else '[red]disabled[/red]'}",
    ]
    console.print(Panel("\n".join(config_text), box=PANEL_BOX, style="cyan", padding=(1, 2)))
    console.print(create_separator_rule())

    # Load base configuration
    base_config = OmegaConf.to_container(cfg, resolve=True)  # type: ignore

    progress_columns = (
        [
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="green", finished_style="bright_green"),
            TimeElapsedColumn(),
        ]
        if UNICODE_OUTPUT
        else [
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
        ]
    )

    with Progress(*progress_columns, console=console) as progress:
        # Step 1: Run Inference
        if not cfg.skip_inference:
            if cfg.use_wandb:
                wandb_run = wandb.init(
                    project=cfg.wandb_project,
                    job_type="inference",
                    name=f"{cfg.tag} inference",
                    config=base_config["inference"],
                )
            console.print(create_step_header("Inference", 1, "blue"))
            run_command_with_progress(
                run_inference,
                (resolve_cfg(cfg.inference),),
                "Running inference...",
                progress,
                style="blue",
                data_path=cfg.data_path,
            )

            console.print(Panel(ui_text("🔍 Generating trajectories visualization...", "Generating trajectories visualization..."), style="blue", box=PANEL_BOX))
            try:
                traj_html = generate_trajectories_html_from_hf(
                    traj_dir=f"{cfg.run_name}/trajectories",
                    repo_id=base_config["inference"]["hf"]["repo_id"],
                    no_cache=True,
                )
                if cfg.use_wandb:
                    wandb.log({"trajectories_viewer": wandb.Html(traj_html)})
                    wandb_run.finish()
            except Exception as e:
                logging.warning(f"Trajectory visualization failed (non-fatal): {e}")

            # Track artifact
            artifacts.append(("Inference", base_config["inference"]["hf"]["repo_id"], f"{cfg.run_name}/trajectories"))
            console.print(create_separator_rule())

        # Step 2: Process Trajectories
        if not cfg.skip_processing:
            if cfg.use_wandb:
                wandb_run = wandb.init(
                    project=cfg.wandb_project,
                    job_type="scripts",
                    name=f"{cfg.tag} scripts",
                )
            console.print(create_step_header("Processing", 2, "green"))
            local_scripts_path = os.path.join(cfg.tmp_dir, f"scripts-{cfg.run_name}", "scripts.jsonl")
            local_traj_dir = base_config["inference"].get("logging_dir")
            run_command_with_progress(
                process_trajectories_to_scripts,
                (
                    base_config["inference"]["hf"]["repo_id"],
                    cfg.run_name,
                    local_scripts_path,
                    local_traj_dir,
                ),
                "Processing trajectories...",
                progress,
                style="green",
                data_path=cfg.data_path,
            )
            console.print(Panel(ui_text("📝 Generating scripts visualization...", "Generating scripts visualization..."), style="green", box=PANEL_BOX))
            try:
                scripts_html = generate_scripts_html_from_hf(
                    scripts_file=f"{cfg.run_name}/scripts.jsonl",
                    repo_id=base_config["inference"]["hf"]["repo_id"],
                    no_cache=True,
                )
                if cfg.use_wandb:
                    wandb.log({"scripts_viewer": wandb.Html(scripts_html)})
                    wandb_run.finish()
            except Exception as e:
                logging.warning(f"Scripts visualization failed (non-fatal): {e}")

            # Track artifact
            artifacts.append(("Processing", base_config["inference"]["hf"]["repo_id"], f"{cfg.run_name}/scripts.jsonl"))
            console.print(create_separator_rule())

        # Step 3: Evaluation
        if not cfg.skip_evaluation:
            # If processing produced a local scripts.jsonl, use it instead of HF
            if not cfg.skip_processing and 'local_scripts_path' in dir() and os.path.isfile(local_scripts_path):
                with open_dict(cfg):
                    cfg.evaluation.input.mode = "local"
                    cfg.evaluation.input.local = local_scripts_path
                base_config["evaluation"] = OmegaConf.to_container(cfg.evaluation, resolve=True)  # type: ignore
                logging.info(f"Evaluation will read scripts from local file: {local_scripts_path}")

            if cfg.use_wandb:
                wandb_run = wandb.init(
                    project=str(cfg.wandb_project),
                    job_type="evaluation",
                    name=f"{cfg.tag} eval",
                    config=base_config["evaluation"],
                )
            console.print(create_step_header("Evaluation", 3, "yellow"))
            run_command_with_progress(
                run_evaluation,
                (resolve_cfg(cfg.evaluation),),
                "Running evaluation...",
                progress,
                style="yellow",
                data_path=cfg.data_path,
                count_pattern=r"Found\s\d+\sissues",  # Add pattern for counting repositories
            )

            console.print(Panel(ui_text("📊 Generating evaluation visualization...", "Generating evaluation visualization..."), style="yellow", box=PANEL_BOX))
            try:
                eval_html = generate_logs_html_from_hf(
                    logs_file=f"{cfg.run_name}/results.jsonl",
                    repo_id=base_config["evaluation"]["output"]["hf"]["repo_id"],
                    no_cache=True,
                )
                if cfg.use_wandb:
                    wandb.log({"evaluation_viewer": wandb.Html(eval_html)})
                    wandb_run.finish()
            except Exception as e:
                logging.warning(f"Evaluation visualization failed (non-fatal): {e}")

            # Track artifact
            artifacts.append(
                ("Evaluation", base_config["evaluation"]["output"]["hf"]["repo_id"], f"{cfg.run_name}/results.jsonl")
            )
            console.print(create_separator_rule())

    # Print artifacts summary table
    if artifacts:
        console.print("\n")
        console.print(create_summary_table(artifacts))
        console.print("\n")

    # Print completion message
        console.print(
            Panel(
                Align.center(f"[bold]{ui_text('🎉 Pipeline completed successfully! 🎉', 'Pipeline completed successfully!')}[/bold]"),
                style="bold green",
                box=HEADER_BOX,
                padding=(1, 2),
            )
        )


if __name__ == "__main__":
    main()
