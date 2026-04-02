import abc
from abc import abstractmethod
from typing import List, Tuple, Union

from langchain_core.tools import BaseTool, BaseToolkit
from pydantic import Field

from inference.src.async_bash_executor import AsyncBashExecutor
from inference.src.native_shell_executor import NativeShellExecutor

# Docker executor 또는 native executor 모두 수용하는 타입
BashExecutor = Union[AsyncBashExecutor, NativeShellExecutor]


class BaseEnvSetupToolkit(BaseToolkit, abc.ABC):
    class Config:
        arbitrary_types_allowed = True

    bash_executor: BashExecutor = Field(
        description="Executor instance for running Bash commands (Docker or native OS)."
    )

    @classmethod
    async def create(
        cls,
        bash_executor: BashExecutor,
    ):
        tools_provider = cls(bash_executor=bash_executor)

        # run initial commands
        for command in tools_provider.initial_commands():
            result, err_code = await tools_provider._execute_bash_command(command)
            if err_code != 0:
                raise ValueError(f"Couldn't execute initial command {command}. Output: {result}")
        return tools_provider

    @property
    def commands_history(self):
        return self.bash_executor.commands_history

    async def clean(self) -> None:
        await self.bash_executor.clean()

    async def _execute_bash_command(self, command: str, add_to_history: bool = True) -> Tuple[str, int]:
        return await self.bash_executor.execute_bash_command(command, add_to_history=add_to_history)

    def initial_commands(self) -> list[str]:
        """
        Commands that are executed upon executor start.
        Can use repository-specific variables.
        Can be overridden and extended in subclasses
        """
        return []

    @abstractmethod
    def get_tools(self, *args, **kwargs) -> List[BaseTool]: ...
