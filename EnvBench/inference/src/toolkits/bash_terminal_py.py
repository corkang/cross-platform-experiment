import os

from .bash_terminal import BashTerminalToolkit


class PythonBashTerminalToolkit(BashTerminalToolkit):
    def initial_commands(self):
        base = super().initial_commands()
        # In native mode pyenv is not guaranteed to be installed on the
        # runner (ubuntu-22.04, macos-14, windows-2022).  Only init if
        # it is actually present so the agent can decide how to proceed.
        if os.environ.get("EXECUTION_MODE") == "native":
            return base + ['command -v pyenv &>/dev/null && eval "$(pyenv init -)" || true']
        return base + ['eval "$(pyenv init -)"']
