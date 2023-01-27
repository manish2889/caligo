import asyncio
import re
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    List,
    Match,
    Optional,
    Pattern,
    Sequence,
    Union,
)

from pyrogram.types import Chat, Message

from caligo import util

if TYPE_CHECKING:
    from .core import Caligo

CommandFunc = Union[
    Callable[..., Coroutine[Any, Any, None]], Callable[..., Coroutine[Any, Any, Any]]
]
Decorator = Callable[[CommandFunc], CommandFunc]


def desc(_desc: str) -> Decorator:
    """Sets description on a command function."""

    def desc_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_description", _desc)
        return func

    return desc_decorator


def usage(_usage: str, optional: bool = False, reply: bool = False) -> Decorator:
    """Sets argument usage help on a command function."""

    def usage_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_usage", _usage)
        setattr(func, "_cmd_usage_optional", optional)
        setattr(func, "_cmd_usage_reply", reply)
        return func

    return usage_decorator


def alias(*aliases: str) -> Decorator:
    """Sets aliases on a command function."""

    def alias_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_aliases", aliases)
        return func

    return alias_decorator


def pattern(_pattern: str) -> Decorator:
    """Sets regex pattern on a command function."""

    def pattern_decorator(func: CommandFunc) -> CommandFunc:
        setattr(func, "_cmd_pattern", re.compile(_pattern))
        return func

    return pattern_decorator


class Command:
    name: str
    desc: Optional[str]
    usage: Optional[str]
    usage_optional: bool
    usage_reply: bool
    aliases: Sequence[str]
    pattern: Optional[Pattern[str]]
    module: Any
    func: CommandFunc

    def __init__(self, name: str, mod: Any, func: CommandFunc) -> None:
        self.name = name
        self.desc = getattr(func, "_cmd_description", None)
        self.usage = getattr(func, "_cmd_usage", None)
        self.usage_optional = getattr(func, "_cmd_usage_optional", False)
        self.usage_reply = getattr(func, "_cmd_usage_reply", False)
        self.aliases = getattr(func, "_cmd_aliases", [])
        self.pattern = getattr(func, "_cmd_pattern", None)
        self.module = mod
        self.func = func


class Context:
    bot: "Caligo"
    chat: Chat
    msg: Message
    reply_msg: Optional[Message]
    segments: Sequence[str]
    cmd_len: int
    invoker: str

    last_update_time: Optional[datetime]

    response: Message
    response_mode: Optional[str]

    input: str
    args: Sequence[str]
    matches: List[Match[str]]

    def __init__(
        self,
        bot: "Caligo",
        msg: Message,
        segments: Sequence[str],
        cmd_len: int,
        matches: List[Match[str]],
    ) -> None:
        self.bot = bot
        self.chat = msg.chat
        self.msg = msg
        self.reply_msg = msg.reply_to_message
        self.segments = segments
        self.cmd_len = cmd_len
        self.invoker = segments[0]

        self.last_update_time = None

        self.response = None  # type: ignore
        self.response_mode = None

        self.input = self.msg.text[self.cmd_len :]
        self.matches = matches

    def __getattr__(self, name: str) -> Any:
        if name == "args":
            return self._get_args()

        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    # Argument segments
    def _get_args(self) -> Sequence[str]:
        self.args = self.segments[1:]
        return self.args

    async def respond(
        self,
        text: str = "",
        *,
        mode: Optional[str] = None,
        redact: bool = True,
        msg: Optional[Message] = None,
        reuse_response: bool = False,
        delete_after: Union[int, float] = 0,
        **kwargs: Any,
    ) -> Message:

        self.response = await self.bot.respond(
            msg or self.msg,
            text,
            input_arg=self.input,
            mode=mode,
            redact=redact,
            response=self.response
            if reuse_response and mode == self.response_mode
            else None,
            **kwargs,
        )
        self.response_mode = mode

        if delete_after != 0:

            async def delete() -> int:
                await asyncio.sleep(delete_after)
                return await self.response.delete()

            self.bot.loop.create_task(delete())

        return self.response

    async def respond_split(
        self,
        text: str,
        *,
        max_pages: Optional[int] = None,  # type: ignore
        redact: Optional[bool] = None,
        **kwargs: Any,
    ) -> Message:
        if redact is None:
            redact = self.bot.config["bot"]["redact_responses"]

        if max_pages is None:
            max_pages: int = self.bot.config["bot"]["overflow_page_limit"]

        if redact:
            # Redact before splitting in case the sensitive content is on a message boundary
            text = self.bot.redact_message(text)

        pages_sent = 0
        last_msg: Message = None  # type: ignore
        while text and pages_sent < max_pages:
            # Make sure that there's an ellipsis placed at both the beginning and end,
            # depending on whether there's more content to be shown
            # The conditions are a bit complex, so just use a primitive LUT for now
            if len(text) <= 4096:
                # Low remaining content might require no ellipses
                if pages_sent == 0:
                    page = text[: util.tg.MESSAGE_CHAR_LIMIT]
                    ellipsis_chars = 0
                else:
                    page = "..." + text[: util.tg.MESSAGE_CHAR_LIMIT - 3]
                    ellipsis_chars = 3
            elif pages_sent == max_pages - 1:
                # Last page should use the standard truncation path if it's too large
                if pages_sent == 0:
                    page = text
                    ellipsis_chars = 0
                else:
                    page = "..." + text
                    ellipsis_chars = 3
            else:
                # Remaining content in other pages might need two ellipses
                if pages_sent == 0:
                    page = text[: util.tg.MESSAGE_CHAR_LIMIT - 3] + "..."
                    ellipsis_chars = 3
                else:
                    page = "..." + text[: util.tg.MESSAGE_CHAR_LIMIT - 6] + "..."
                    ellipsis_chars = 6

            last_msg = await self.respond_multi(page, **kwargs)
            text = text[util.tg.MESSAGE_CHAR_LIMIT - ellipsis_chars :]
            pages_sent += 1

        return last_msg

    async def respond_multi(
        self,
        *args: Any,
        mode: Optional[str] = None,
        msg: Message = None,  # type: ignore
        reuse_response: bool = False,
        **kwargs: Any,
    ) -> Message:
        # First response is the same
        if self.response:
            # After that, force a reply to the previous response
            if mode is None:
                mode = "reply"

            if msg is None:
                msg = self.response

            if reuse_response is None:
                reuse_response = False

        return await self.respond(
            *args, mode=mode, msg=msg, reuse_response=reuse_response, **kwargs
        )
