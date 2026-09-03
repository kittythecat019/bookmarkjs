"""
Verity in-game bridge for Android Termux via /connect.

Uses groq_http (httpx) — NOT the openai SDK.
Mic: Termux:API via mic.py / android_mic.py
"""
import base64
import tempfile
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from uuid import uuid4

from dotenv import load_dotenv
import websockets

from mic import list_input_devices, mic_backend_name, record_microphone_wav
from fish_tts import (
    finish_fish,
    fish_tts_configured,
    play_wav_file,
    prepare_fish,
    purge_old_tts_files,
    resolve_fish_style,
    speak_fish,
    strip_fish_tags,
)
from groq_http import chat_completion, transcribe_audio
from verity_persona import (
    build_system_prompt,
    get_locked_language,
    language_to_whisper_code,
    resolve_reply_language,
    infer_session_language_from_text,
    try_lock_language_from_text,
)

load_dotenv()

PORT = int(os.getenv("VERITY_WS_PORT", "3000"))
# Localhost only — other devices on Wi-Fi cannot /connect to this bridge.
WS_HOST = (os.getenv("VERITY_WS_HOST", "127.0.0.1").strip() or "127.0.0.1")
LISTEN_SECONDS = float(os.getenv("VERITY_LISTEN_SECONDS", "5"))
# "auto" = mirror whatever language the player speaks/types (any language).
# Set VERITY_LANGUAGE=vietnamese (etc.) only if you want to force one reply language.
LANGUAGE = os.getenv("VERITY_LANGUAGE", "auto").strip() or "auto"
STT_MODEL = os.getenv("VERITY_STT_MODEL", "whisper-large-v3").strip()
LLM_MODEL = os.getenv("VERITY_LLM_MODEL", "openai/gpt-oss-20b").strip()
LLM_MAX_TOKENS = int(os.getenv("VERITY_LLM_MAX_TOKENS", "180"))
LLM_TEMPERATURE = float(os.getenv("VERITY_LLM_TEMPERATURE", "0.75"))


def _relax_websocket_close_codes() -> None:
    """Minecraft Bedrock (Android especially) often closes with a non-RFC code."""
    for mod_name in ("websockets.frames", "websockets.framing", "websockets.legacy.framing"):
        try:
            mod = __import__(mod_name, fromlist=["Close"])
        except ImportError:
            continue
        Close = getattr(mod, "Close", None)
        if Close is None or not hasattr(Close, "check"):
            continue
        if getattr(Close.check, "_verity_patched", False):
            continue
        orig = Close.check

        def check(self, _orig=orig):
            try:
                _orig(self)
            except Exception:
                return

        check._verity_patched = True  # type: ignore[attr-defined]
        Close.check = check  # type: ignore[method-assign]


GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
RENDER_AI_URL = os.getenv(
    "VERITY_AI_URL",
    "https://verity-server-for-minecraft.onrender.com/ai"
).strip()
_last_render_audio = None
_last_render_audio_format = "wav"
_history: list[dict[str, str]] = []
_busy = False
_last_talk_at = 0.0
TALK_DEBOUNCE_SEC = 6.0


TALK_RE = re.compile(r"^!talk\b", re.I)
TEXT_RE = re.compile(r"^!(?:verity|v)\s+(.+)$", re.I)

# "say dog 100 times" / "nói skibidi toilet 100 lần"
_REPEAT_SAY_RE = re.compile(
    r"(?:(?:can|could|would)\s+you\s+|please\s+)?"
    r"(?:say|repeat|n[oó]i(?:\s+l[aạ]i)?)\s+"
    r"(.+?)\s+"
    r"(\d{1,4})\s+"
    r"(?:times|time|l[aầ]n)\b",
    re.I,
)
_REPEAT_SAY_SKIP = re.compile(
    r"^(it|this|that|something|that again|the same)$",
    re.I,
)
MAX_SAY_REPEAT = 100
MAX_SAY_PHRASE = 40

# Route speech through the addon first so story steps, item requests and
# come here / follow keep working. Groq only answers what the addon cannot.
USE_ADDON_PIPELINE = os.getenv("VERITY_USE_ADDON", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
AI_NEEDED_TAG = "pntmc_ai_needed"
AI_DONE_TAG = "pntmc_ai_done"
AI_FLAVOR_TAG = "pntmc_ai_flavor"
AI_IGNORE_TAG = "pntmc_ai_ignore"
AI_SILENT_TAG = "pntmc_ai_silent"
BRIDGE_ON_TAG = "pntmc_bridge_on"
WANT_TALK_TAG = "pntmc_want_talk"
HURT_SPEAK_TAG = "pntmc_hurt_speak"
ADDON_WAIT_SEC = float(os.getenv("VERITY_ADDON_WAIT_SEC", "3.0"))
ADDON_POLL_SEC = 0.25
SCRIPTEVENT_MAX_CHARS = 200


def _system_prompt(
    detected_lang: str | None = None,
    *,
    flavor_mode: bool = False,
    addon_context: str | None = None,
    phase: int = 1,
) -> str:
    return build_system_prompt(
        LANGUAGE,
        detected_lang,
        flavor_mode=flavor_mode,
        addon_context=addon_context,
        phase=phase,
    )


def _ask_llm(
    user_text: str,
    detected_lang: str | None = None,
    *,
    flavor_mode: bool = False,
    addon_context: str | None = None,
    history_user: str | None = None,
    phase: int = 1,
) -> str:
    try_lock_language_from_text(history_user or user_text)
    _, lang_hint = resolve_reply_language(LANGUAGE, detected_lang)

    messages = [
        {
            "role": "system",
            "content": _system_prompt(
                detected_lang,
                flavor_mode=flavor_mode,
                addon_context=addon_context,
                phase=phase,
            ),
        }
    ]

    messages.extend(_history[-12:])
    messages.append({
        "role": "user",
        "content": user_text
    })

    print(
        f"[render] chat lang={lang_hint!r} "
        f"locked={get_locked_language()!r} "
        f"flavor={flavor_mode} phase={phase} "
        f"ctx={addon_context!r} q={user_text[:80]!r}",
        flush=True,
    )

    # Gửi toàn bộ context/history lên Render
    payload = {
        "messages": messages,
        "message": user_text,
        "detected_lang": detected_lang,
        "flavor_mode": flavor_mode,
        "addon_context": addon_context,
        "phase": phase,
    }

    try:
        response = httpx.post(
            RENDER_AI_URL,
            json=payload,
            timeout=120.0,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Render AI connection error: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Render AI HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"Render returned invalid JSON: {response.text[:500]}"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(
            f"Render AI error: {data.get('error', 'Unknown error')}"
        )
global _last_render_audio, _last_render_audio_format

_last_render_audio = data.get("audio")
_last_render_audio_format = data.get("format", "wav")
    reply = str(data.get("reply", "")).strip()

    if not reply:
        raise RuntimeError("Render returned an empty AI reply")

    if len(reply) >= 2 and reply[0] == reply[-1] and reply[0] in "\"'":
        reply = reply[1:-1].strip()

    _history.append({
        "role": "user",
        "content": history_user or user_text,
    })

    _history.append({
        "role": "assistant",
        "content": reply,
    })

    print(
        f"[render] ok ({len(reply)} chars)",
        flush=True,
    )

    return reply


def _transcribe(wav_path: str) -> tuple[str, str | None]:
    """
    Whisper auto-detects language (any). Returns (text, language_code).
    Force language from env or from a session lock the player requested.
    """
    force_lang = None
    if LANGUAGE.lower() not in ("auto", "detect", "*"):
        name = LANGUAGE.lower()
        aliases = {
            "vietnamese": "vi",
            "english": "en",
            "spanish": "es",
            "portuguese": "pt",
            "russian": "ru",
            "japanese": "ja",
            "korean": "ko",
            "chinese": "zh",
            "french": "fr",
            "german": "de",
            "thai": "th",
            "indonesian": "id",
        }
        force_lang = aliases.get(name, name if len(name) == 2 else None)
    else:
        force_lang = language_to_whisper_code(get_locked_language())

    return transcribe_audio(
        wav_path,
        model=STT_MODEL,
        language=force_lang,
    )


def _escape_tellraw(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def _pad_command(command_line: str) -> str:
    # Bedrock /connect drops the FIRST byte of commandLine ("tellraw" → "ellraw").
    # Prefix a single "/" so after the drop you get a valid bare command ("tellraw").
    # Do NOT use "//" — that becomes "/tellraw", which the WS rejects (no in-game chat).
    cmd = str(command_line or "").strip().lstrip("/")
    return "/" + cmd


def _cmd_payload(command_line: str, request_id: str | None = None) -> str:
    cmd = _pad_command(command_line)
    return json.dumps(
        {
            "header": {
                "version": 1,
                "requestId": request_id or str(uuid4()),
                "messagePurpose": "commandRequest",
                "messageType": "commandRequest",
            },
            "body": {
                "version": 1,
                "commandLine": cmd,
                "origin": {"type": "player"},
            },
        }
    )


async def send_cmd(ws, command_line: str) -> None:
    req_id = str(uuid4())
    payload = _cmd_payload(command_line, req_id)
    # Track so the reader can log failures.
    pending = getattr(ws, "_verity_pending", None)
    if isinstance(pending, dict):
        pending[req_id] = command_line[:80]
    try:
        await ws.send(payload)
    except websockets.exceptions.ConnectionClosed:
        print(
            f"[cmd] skip (disconnected) {_pad_command(command_line)[:90]!r}",
            flush=True,
        )
        return
    print(f"[cmd] → {_pad_command(command_line)[:90]!r}", flush=True)


async def run_command(ws, command_line: str, timeout: float = 3.0) -> dict:
    """Send a command and wait for its commandResponse body."""
    req_id = str(uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    futures = getattr(ws, "_verity_futures", None)
    if isinstance(futures, dict):
        futures[req_id] = future

    await ws.send(_cmd_payload(command_line, req_id))
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        return {}
    finally:
        if isinstance(futures, dict):
            futures.pop(req_id, None)


def _clean_for_scriptevent(text: str) -> str:
    value = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    return re.sub(r"\s{2,}", " ", value).strip()


def _chunk_for_scriptevent(text: str, max_chunks: int = 16) -> list[str]:
    clean = _clean_for_scriptevent(text)
    if not clean:
        return []
    if len(clean) <= SCRIPTEVENT_MAX_CHARS:
        return [clean]

    chunks: list[str] = []
    current = ""
    for word in clean.split(" "):
        if not word:
            continue
        piece = word
        while len(piece) > SCRIPTEVENT_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:SCRIPTEVENT_MAX_CHARS])
            piece = piece[SCRIPTEVENT_MAX_CHARS:]
        extra = 1 if current else 0
        if current and len(current) + extra + len(piece) > SCRIPTEVENT_MAX_CHARS:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip() if current else piece
    if current:
        chunks.append(current)
    return chunks[: max(1, max_chunks)]


async def addon_ask(ws, player_name: str, text: str) -> None:
    """Replay the player's speech through the addon's own chat pipeline."""
    name = _clean_for_scriptevent(player_name).replace("|", "")
    payload = _clean_for_scriptevent(text)[:SCRIPTEVENT_MAX_CHARS]
    await send_cmd(ws, f"scriptevent pntmc:ai_ask {name}|{payload}")


async def addon_say(
    ws,
    player_name: str,
    text: str,
) -> None:
    """
    Let the addon show chat + mouth animation.
    First chunk: no tick override — scripts use 1 tick per character.
    Later chunks: ticks=0 so extra lines print without restarting the mouth.
    """
    name = _clean_for_scriptevent(player_name).replace("|", "")
    display = strip_fish_tags(text)
    chunks = _chunk_for_scriptevent(display)
    for index, chunk in enumerate(chunks):
        if index:
            await asyncio.sleep(0.15)
            await send_cmd(ws, f"scriptevent pntmc:ai_say {name}|0|{chunk}")
        else:
            await send_cmd(ws, f"scriptevent pntmc:ai_say {name}|{chunk}")


async def addon_mouth_only(ws, player_name: str, text: str) -> None:
    """Open the ball mouth without printing extra chat lines."""
    name = _clean_for_scriptevent(player_name).replace("|", "")
    ticks = max(12, min(450, len(strip_fish_tags(text))))
    await send_cmd(ws, f"scriptevent pntmc:ai_say {name}|{ticks}|__MOUTH_ONLY__")


DROP_OK_TAG = "pntmc_drop_ok"
DROP_FAIL_TAG = "pntmc_drop_fail"
DROP_BLOCK_TAG = "pntmc_drop_block"
DROP_INV_TAG = "pntmc_drop_inv"
_DROP_RESULT_TAGS = (DROP_OK_TAG, DROP_FAIL_TAG, DROP_BLOCK_TAG, DROP_INV_TAG)

_DROP_CMD_RE = re.compile(
    r"^\s*\[\[DROP:([a-z][a-z0-9_]{0,40}):(\d{1,2})\]\]\s*",
    re.I,
)
_DROP_SPECIAL_RE = re.compile(
    r"^\s*\[\[DROP:(refuse|unknown|none)\]\]\s*",
    re.I,
)
_ITEM_ASK_RE = re.compile(
    r"\b(give|drop|spawn|throw|i need|i want|cho|dua|nem|tha|muon|can)\b",
    re.I,
)


async def addon_drop(ws, player_name: str, item_id: str, amount: int) -> str:
    """Ask addon to drop items. Returns ok|block|inv|fail."""
    name = _clean_for_scriptevent(player_name).replace("|", "")
    item = re.sub(r"[^a-z0-9_]", "", str(item_id or "").lower().replace("minecraft:", ""))
    qty = max(1, min(64, int(amount or 10)))
    if not item:
        return "fail"
    for tag in _DROP_RESULT_TAGS:
        await send_cmd(ws, f"tag @a[tag={tag}] remove {tag}")
    await send_cmd(ws, f"scriptevent pntmc:ai_drop {name}|{item}|{qty}")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.12)
        if await _tag_present(ws, DROP_OK_TAG):
            await send_cmd(ws, f"tag @a[tag={DROP_OK_TAG}] remove {DROP_OK_TAG}")
            return "ok"
        if await _tag_present(ws, DROP_BLOCK_TAG):
            await send_cmd(ws, f"tag @a[tag={DROP_BLOCK_TAG}] remove {DROP_BLOCK_TAG}")
            return "block"
        if await _tag_present(ws, DROP_INV_TAG):
            await send_cmd(ws, f"tag @a[tag={DROP_INV_TAG}] remove {DROP_INV_TAG}")
            return "inv"
        if await _tag_present(ws, DROP_FAIL_TAG):
            await send_cmd(ws, f"tag @a[tag={DROP_FAIL_TAG}] remove {DROP_FAIL_TAG}")
            return "fail"
    print("[addon] drop result timeout", flush=True)
    return "fail"


def _needs_groq_drop_command(prep: dict, user_text: str) -> bool:
    """When addon did not already drop, let Groq map speech → item id."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    acted = bool(prep.get("acted"))
    draft = str(prep.get("draft") or "").lower()
    if acted and intent == "drop_item":
        return False
    if acted and intent == "drop_item_blocked":
        return False
    if acted and intent == "drop_item_unknown":
        if "inventory" in draft or "put me" in draft:
            return False
        return True
    if not acted and _ITEM_ASK_RE.search(str(user_text or "")):
        return True
    return False


def _extract_drop_command(reply: str) -> tuple[str, dict | None]:
    """
    Strip leading [[DROP:id:n]] / [[DROP:refuse|unknown|none]].
    Returns (spoken_reply, command_dict_or_None).
    """
    text = str(reply or "")
    m = _DROP_CMD_RE.match(text)
    if m:
        item = m.group(1).lower()
        amount = max(1, min(64, int(m.group(2))))
        spoken = text[m.end() :].strip()
        return spoken, {"kind": "item", "id": item, "amount": amount}
    m2 = _DROP_SPECIAL_RE.match(text)
    if m2:
        spoken = text[m2.end() :].strip()
        return spoken, {"kind": m2.group(1).lower()}
    return text.strip(), None


async def _apply_groq_drop_command(
    ws, player_name: str, prep: dict, user_text: str, reply: str
) -> str:
    if not _needs_groq_drop_command(prep, user_text):
        spoken, cmd = _extract_drop_command(reply)
        # Ignore accidental DROP tokens when not needed
        return spoken if cmd else reply

    spoken, cmd = _extract_drop_command(reply)
    if not cmd:
        print("[addon] Groq omitted [[DROP:...]] — no bridge drop", flush=True)
        return spoken or reply

    if cmd["kind"] == "refuse":
        return spoken or "Go find that yourself."
    if cmd["kind"] == "unknown":
        return spoken or "What item do you mean?"
    if cmd["kind"] == "none":
        return spoken or reply

    item_id = str(cmd.get("id") or "")
    amount = int(cmd.get("amount") or 10)
    print(f"[addon] Groq DROP {item_id} x{amount}", flush=True)
    result = await addon_drop(ws, player_name, item_id, amount)
    pretty = item_id.replace("_", " ")
    if result == "ok":
        draft = f"Here. {amount} {pretty}."
        return _fix_drop_item_reply(
            {"intent": "drop_item", "acted": True, "draft": draft},
            spoken or draft,
        )
    if result == "block":
        return spoken or "Go find that yourself."
    if result == "inv":
        return spoken or "Put me on the ground first. Then ask again."
    return spoken or f"I can't throw {pretty}."


async def speak_fish_audio_only(
    ws,
    reply: str,
    *,
    style: str = "normal",
    hold_until: float = 0.0,
) -> None:
    """Play Fish TTS without a second chat line (addon already printed the story)."""
    chat_text = strip_fish_tags(reply)
    if not chat_text or not fish_tts_configured():
        return
    path = None
    try:
        await say_actionbar(ws, "Loading voice...")
        path, duration = await asyncio.to_thread(
            lambda: prepare_fish(chat_text, style=style)
        )
        leftover = float(hold_until or 0) - time.monotonic()
        if leftover > 0.05:
            print(f"[addon] hold Fish {leftover:.2f}s for pack/mob sound", flush=True)
            await asyncio.sleep(leftover)
        if path and duration > 0:
            print(
                f"[fish] story-only sec={duration:.2f} style={style}",
                flush=True,
            )
            await asyncio.sleep(0.05)
            await asyncio.to_thread(play_wav_file, path)
        else:
            await asyncio.to_thread(speak_fish, chat_text, style=style)
    finally:
        finish_fish(path)
        try:
            await say_actionbar(ws, "")
        except Exception:  # noqa: BLE001
            pass


async def speak_reply_synced(
    ws,
    player_name: str,
    reply: str,
    *,
    style: str = "normal",
    one_line: bool = False,
    hold_until: float = 0.0,
) -> None:
    """
    Render đã tạo Fish Audio.
    Bridge nhận WAV Base64 từ Render, lưu thành file WAV
    rồi dùng play_wav_file() để phát local.

    Không gọi Fish Audio lần nữa trên máy local.
    """

    global _last_render_audio, _last_render_audio_format

    chat_text = strip_fish_tags(reply)

    if not chat_text:
        return

    path = None

    try:
        # =========================
        # LẤY AUDIO TỪ RENDER
        # =========================

        audio_b64 = _last_render_audio
        audio_format = (
            _last_render_audio_format or "wav"
        ).lower().strip()

        if audio_b64:
            try:
                audio_data = base64.b64decode(audio_b64)

                suffix = ".wav"

                if audio_format == "mp3":
                    suffix = ".mp3"

                fd, path = tempfile.mkstemp(
                    prefix="verity_render_",
                    suffix=suffix,
                )

                os.close(fd)

                with open(path, "wb") as audio_file:
                    audio_file.write(audio_data)

                print(
                    f"[render] audio received "
                    f"format={audio_format} "
                    f"bytes={len(audio_data)}",
                    flush=True,
                )

            except Exception as exc:
                print(
                    f"[render] audio decode failed: {exc}",
                    flush=True,
                )
                path = None

        # Audio này chỉ dùng một lần
        _last_render_audio = None
        _last_render_audio_format = "wav"

        # =========================
        # CHỜ MOB SOUND
        # =========================

        leftover = float(hold_until or 0) - time.monotonic()

        if leftover > 0.05:
            print(
                f"[addon] hold voice {leftover:.2f}s for mob sound",
                flush=True,
            )
            await asyncio.sleep(leftover)

        # =========================
        # HIỆN CHAT
        # =========================

        if one_line:
            await say_verity(ws, chat_text)
        else:
            chunks = _chunk_for_scriptevent(chat_text)

            if not chunks:
                await say_verity(ws, chat_text)
            else:
                for index, chunk in enumerate(chunks):
                    if index:
                        await asyncio.sleep(0.12)

                    await say_verity(ws, chunk)

        # =========================
        # MOUTH ANIMATION
        # =========================

        if USE_ADDON_PIPELINE:
            await addon_mouth_only(
                ws,
                player_name,
                chat_text,
            )

        # =========================
        # PHÁT AUDIO
        # =========================

        if path:
            await asyncio.sleep(0.05)

            if audio_format == "wav":
                await asyncio.to_thread(
                    play_wav_file,
                    path,
                )
            else:
                print(
                    f"[render] unsupported audio format: "
                    f"{audio_format}",
                    flush=True,
                )

    except Exception as exc:
        print(
            f"[render] speak error: {exc}",
            flush=True,
        )

    finally:
        # Xóa file tạm
        if path:
            try:
                os.remove(path)
            except Exception:
                pass

        try:
            await say_actionbar(ws, "")
        except Exception:
            pass


async def speak_hurt_synced(ws, player_name: str, reply: str) -> None:
    """
    Punch/bounce ouch: Fish TTS + chat only.
    Keep hurt face — never open talk mouth / face 2.
    """
    chat_text = strip_fish_tags(reply)
    if not chat_text:
        return

    path = None
    try:
        if fish_tts_configured():
            path, duration = await asyncio.to_thread(
                lambda: prepare_fish(chat_text, style="angry")
            )
            print(
                f"[fish] hurt ready duration={duration:.2f}s text={chat_text!r}",
                flush=True,
            )

        # tellraw first — same as normal replies (ai_hurt can drop on flaky WS).
        await say_verity(ws, chat_text)
        name = _clean_for_scriptevent(player_name).replace("|", "")
        payload = _clean_for_scriptevent(chat_text)[:SCRIPTEVENT_MAX_CHARS]
        # Still notify addon (hurt face hooks); chat already printed above.
        try:
            await send_cmd(ws, f"scriptevent pntmc:ai_hurt {name}|{payload}")
        except Exception as exc:  # noqa: BLE001
            print(f"[ws] ai_hurt skip: {exc}", flush=True)

        if path:
            await asyncio.sleep(0.05)
            await asyncio.to_thread(play_wav_file, path)
        elif fish_tts_configured():
            await asyncio.to_thread(speak_fish, chat_text, style="angry")
    finally:
        finish_fish(path)


def _player_name_from_tag_message(msg: str) -> str:
    for token in msg.replace(",", " ").split():
        if token.lower() in (
            "has",
            "have",
            "the",
            "following",
            "tags",
            "players",
            "and",
        ):
            continue
        if token.startswith("pntmc_"):
            continue
        if token and token[0].isalpha():
            return token
    return "Player"


async def _tag_present(ws, tag: str) -> bool:
    body = await run_command(ws, f"tag @a[tag={tag}] list", timeout=2.0)
    if not body:
        return False
    status_message = str(body.get("statusMessage") or "")
    return int(body.get("successCount") or 0) > 0 or tag in status_message


async def _tag_list_message(ws) -> str:
    body = await run_command(ws, "tag @a list", timeout=2.0)
    if not body:
        return ""
    return str(body.get("statusMessage") or "")


_LOOK_BLOCK_ASK_RE = re.compile(
    r"\b(what(?:'s| is|s) (?:this|that|the) block|what block is (?:this|that)|"
    r"block is this|this block|that block)\b"
    r"|đây là (?:cái )?block|block (?:này |đó )?(?:là )?gì",
    re.I,
)


def _user_asks_look_block(text: str) -> bool:
    return bool(_LOOK_BLOCK_ASK_RE.search(str(text or "")))


_LOOK_ENTITY_ASK_RE = re.compile(
    r"\bwhat(?:'s| is|s) (?:this|that)\s+(?:entity|mob|animal|creature)\b|"
    r"\bwhat (?:entity|mob|animal|creature) is (?:this|that)\b|"
    r"đây là con gì|con này là gì|con kia là gì",
    re.I,
)
_KNOWN_MOBS_RE = re.compile(
    r"\b(zombie|creeper|skeleton|spider|enderman|witch|villager|cow|pig|sheep|"
    r"chicken|wolf|horse|cat|iron golem|pillager|warden|blaze|ghast|drowned|"
    r"husk|stray|phantom|slime|magma cube|guardian|ravager|vindicator|evoker|"
    r"bee|fox|goat|frog|axolotl|panda|llama|camel|sniffer)\b",
    re.I,
)


def _user_asks_look_entity(text: str) -> bool:
    return bool(_LOOK_ENTITY_ASK_RE.search(str(text or "")))


_IDENTITY_ASK_RE = re.compile(
    r"\b(who are you|what are you|your name|who're you)\b|"
    r"bạn là ai|ban la ai|mày là ai|may la ai|verity là ai|verity la ai|"
    r"tên (bạn|mày) là gì|ten (ban|may) la gi",
    re.I,
)
_PNTMC_ASK_RE = re.compile(
    r"\bwho(?:'s| is|s) pntmc\b|\bwhat(?:'s| is|s) pntmc\b|"
    r"pntmc.{0,16}(là ai|la ai)|(ai là|ai la).{0,16}pntmc",
    re.I,
)
_MISS_HIM_RE = re.compile(
    r"i miss him|mob\.\.\.|nho anh|nhớ anh|tôi nhớ",
    re.I,
)
_PNTMC_IDK_RE = re.compile(
    r"don'?t know|do not know|never heard|no idea|khong biet|không biết",
    re.I,
)


def _user_asks_identity(text: str) -> bool:
    raw = str(text or "")
    if _user_asks_pntmc(raw):
        return False
    if re.search(r"\bthatmob\b|that mob", raw, re.I):
        return False
    return bool(_IDENTITY_ASK_RE.search(raw))


def _user_asks_pntmc(text: str) -> bool:
    return bool(_PNTMC_ASK_RE.search(str(text or "")))


def _fix_identity_reply(user_text: str, reply: str) -> str:
    if not _user_asks_identity(user_text):
        return reply
    text = str(reply or "").strip()
    if text and not _MISS_HIM_RE.search(text):
        return text
    print("[addon] identity Groq used miss-him line — correcting", flush=True)
    return "I'm Verity. Ask me anything."


def _fix_pntmc_reply(prep: dict, user_text: str, reply: str) -> str:
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent not in ("pntmc_who", "pntm") and not _user_asks_pntmc(user_text):
        return reply
    text = str(reply or "").strip()
    if text and not _PNTMC_IDK_RE.search(text) and re.search(r"pntmc", text, re.I):
        return text
    print("[addon] PnTMC Groq missed — correcting", flush=True)
    return random.choice(PNTMC_REPLIES)


def _coded_look_parts(prep: dict) -> tuple[str, str]:
    """Read entity/block names from addon draft tags: 'entity sheep' / 'block grass block'."""
    draft = str(prep.get("draft") or "").strip()
    if not draft:
        return "", ""
    parts = draft.split(None, 1)
    if len(parts) < 2:
        return "", ""
    kind = parts[0].lower().strip()
    rest = parts[1].strip()
    if kind in ("e", "mob"):
        kind = "entity"
    if kind in ("b", "blk"):
        kind = "block"
    if not rest or rest.lower() in ("none", "nothing", "nothing nearby"):
        return kind, ""
    return kind, rest


def _forced_look_at_reply(prep: dict) -> str | None:
    """Fallback line if Groq ignores the live look-at name."""
    kind, rest = _coded_look_parts(prep)
    if kind == "entity" and rest:
        pretty = " ".join(w.capitalize() if w else w for w in rest.split())
        return f"That's a {pretty}."
    if kind == "block" and rest:
        pretty = " ".join(w.capitalize() if w else w for w in rest.split())
        return f"That's {pretty}."
    entity = _entity_name_from_prep(prep)
    if entity:
        pretty = " ".join(w.capitalize() if w else w for w in entity.split())
        return f"That's a {pretty}."
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent in ("look_block", "blk"):
        block = _block_name_from_prep(prep)
        if block:
            pretty = " ".join(w.capitalize() if w else w for w in block.split())
            return f"That's {pretty}."
    return None


def _entity_name_from_prep(prep: dict) -> str:
    entity = str(prep.get("entity") or "").strip()
    draft = str(prep.get("draft") or "").strip()
    low = entity.lower()
    if low and low not in ("none", "nothing", "entity none"):
        return entity
    kind, rest = _coded_look_parts(prep)
    if kind == "entity" and rest:
        return rest
    if re.match(r"^block\s+", draft, re.I):
        return ""
    m = re.search(
        r"^(?:looking at|entity)\s+(.+?)(?:\s+block)?$",
        draft,
        re.I,
    )
    if m:
        name = m.group(1).strip()
        if name.lower() not in ("none", "nothing", "nothing nearby"):
            return name
    return ""


def _block_name_from_prep(prep: dict) -> str:
    return _clean_look_block_name(
        str(prep.get("block") or ""),
        str(prep.get("draft") or ""),
    )


_LOOK_GENERIC = frozenset(
    {"block", "entity", "mob", "the", "this", "that", "item", "none", "a"}
)

_MISS_LOOK_REPLY_RE = re.compile(
    r"can'?t see|cannot see|don'?t see|do not see|"
    r"not (?:seeing|pointing)|nothing (?:in|on) (?:your |the )?crosshair|"
    r"no mob|nothing nearby|aim (?:your )?(?:cursor|crosshair)|"
    r"point (?:your )?(?:cursor|crosshair|at)|"
    r"you'?re not pointing|not pointing at|"
    r"don'?t have anything|do not have anything|"
    r"look at a block|point at (?:the |a )?block|"
    r"khong thay|không thấy|khong biet|không biết",
    re.I,
)


def _look_name_mentioned(name: str, text: str) -> bool:
    """True if Groq used the live name or its first real word (e.g. grass from grass block)."""
    n = str(name or "").strip().lower()
    low = str(text or "").lower()
    if not n or not low:
        return False
    if n in low:
        return True
    first = re.split(r"[\s_]+", n)[0]
    if not first or first in _LOOK_GENERIC or len(first) < 3:
        return False
    return bool(re.search(rf"\b{re.escape(first)}\b", low))


def _fix_look_entity_reply(prep: dict, user_text: str, reply: str) -> str:
    """Rewrite only if Groq ignored the live mob or claimed it was missing."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent in ("look_block", "blk") or _user_asks_look_block(user_text):
        return reply
    asked = _user_asks_look_entity(user_text) or intent == "nearby_entity"
    if not asked:
        return reply
    name = _entity_name_from_prep(prep)
    text = str(reply or "").strip()
    if not name:
        return text
    if _look_name_mentioned(name, text) and not _MISS_LOOK_REPLY_RE.search(text):
        return text
    pretty = " ".join(w.capitalize() if w else w for w in name.split())
    print(f"[addon] look-entity Groq missed {name!r} — correcting", flush=True)
    return f"That's a {pretty}."


def _clean_look_block_name(block: str, draft: str) -> str:
    raw = str(block or "").strip()
    if not raw:
        kind, rest = _coded_look_parts({"draft": draft})
        if kind == "block" and rest:
            raw = rest
        else:
            raw = str(draft or "").strip()
    low = re.sub(
        r"^(that s|thats|that's|you re looking at|you are looking at|looking at|block)\s+",
        "",
        raw.lower().strip(),
    )
    if not low or low in ("none", "nothing", "block none"):
        return ""
    return low


_MC_FORMAT_RE = re.compile(r"§.")


def _strip_mc_format(text: str) -> str:
    return _MC_FORMAT_RE.sub("", str(text or ""))


def _parse_prep_from_tag_message(msg: str) -> dict:
    clean = _strip_mc_format(msg)
    intent_raw = "chat"
    intent = "chat"
    draft = ""
    biome = ""
    entity = ""
    block = ""
    phase = 1
    acted = "pntmc_acted" in clean.replace(",", " ")
    for token in re.findall(r"pntmc_[a-z0-9]+(?:_[a-z0-9_]+)?", clean, re.I):
        low = token.lower()
        if low == "pntmc_acted":
            acted = True
        elif low.startswith("pntmc_i_"):
            intent_raw = low[len("pntmc_i_") :] or "chat"
            intent = _normalize_intent(intent_raw)
        elif low.startswith("pntmc_d_"):
            draft = low[len("pntmc_d_") :].replace("_", " ")
        elif low.startswith("pntmc_b_"):
            biome = low[len("pntmc_b_") :].replace("_", " ")
        elif low.startswith("pntmc_e_"):
            entity = low[len("pntmc_e_") :].replace("_", " ")
        elif low.startswith("pntmc_k_"):
            block = low[len("pntmc_k_") :].replace("_", " ")
        elif low.startswith("pntmc_ph_"):
            try:
                phase = max(1, min(4, int(low[len("pntmc_ph_") :])))
            except ValueError:
                phase = 1
    return {
        "intent_raw": intent_raw,
        "intent": intent,
        "acted": acted,
        "draft": draft,
        "phase": phase,
        "biome": biome,
        "entity": entity,
        "block": block,
    }


def _fill_look_names_from_draft(prep: dict) -> None:
    kind, rest = _coded_look_parts(prep)
    if kind == "entity" and rest and not str(prep.get("entity") or "").strip():
        prep["entity"] = rest
    if kind == "block" and rest and not str(prep.get("block") or "").strip():
        prep["block"] = rest


def _look_is_explicit_miss(prep: dict, kind: str) -> bool:
    draft = str(prep.get("draft") or "").strip().lower()
    if kind == "block":
        if _block_name_from_prep(prep):
            return False
        return draft in ("block none", "none", "nothing") or draft.endswith(" none")
    if _entity_name_from_prep(prep):
        return False
    return draft in ("entity none", "none", "nothing", "nothing nearby") or draft.endswith(
        " none"
    )


async def read_addon_prep(ws, user_text: str = "", player_name: str = "") -> dict:
    """
    Read silent prep tags published by the addon after ai_ask.
    Tags: pntmc_acted, pntmc_i_<intent>, pntmc_d_<draft>, pntmc_ph_<phase>, pntmc_b_<biome>, pntmc_e_<entity>, pntmc_k_<block>
    """
    wants_look = _user_asks_look_block(user_text) or _user_asks_look_entity(user_text)
    prep = {
        "intent_raw": "chat",
        "intent": "chat",
        "acted": False,
        "draft": "",
        "phase": 1,
        "biome": "",
        "entity": "",
        "block": "",
    }
    msg = ""
    for attempt in range(3):
        if attempt == 0:
            await asyncio.sleep(0.2)
        else:
            name = _clean_for_scriptevent(player_name or "").replace("|", "") or "Player"
            await send_cmd(ws, f"scriptevent pntmc:prep_query {name}|refresh")
            await asyncio.sleep(0.25)
        msg = await _tag_list_message(ws)
        prep = _parse_prep_from_tag_message(msg)
        _fill_look_names_from_draft(prep)
        has_look_name = bool(_block_name_from_prep(prep) or _entity_name_from_prep(prep))
        if has_look_name or not wants_look:
            break
        print(
            f"[addon] look tags empty attempt={attempt + 1} msg={msg[:240]!r}",
            flush=True,
        )

    intent_raw = str(prep.get("intent_raw") or "chat")
    intent = _normalize_intent(str(prep.get("intent") or "chat"))
    draft = str(prep.get("draft") or "")
    biome = str(prep.get("biome") or "")
    entity = str(prep.get("entity") or "")
    block = str(prep.get("block") or "")
    acted = bool(prep.get("acted"))
    phase = int(prep.get("phase") or 1)

    if _user_asks_look_block(user_text) and intent not in ("look_block", "blk"):
        if await _tag_present(ws, "pntmc_i_blk") or await _tag_present(
            ws, "pntmc_i_look_block"
        ):
            intent_raw = "blk"
            intent = "look_block"
        if not acted:
            acted = await _tag_present(ws, "pntmc_acted")

    if _user_asks_look_entity(user_text) and intent not in ("nearby_entity", "look"):
        if await _tag_present(ws, "pntmc_i_look") or await _tag_present(
            ws, "pntmc_i_nearby_entity"
        ):
            intent_raw = "look"
            intent = "nearby_entity"

    await send_cmd(ws, "tag @a[tag=pntmc_acted] remove pntmc_acted")
    if intent_raw and intent_raw != "chat":
        await send_cmd(ws, f"tag @a[tag=pntmc_i_{intent_raw}] remove pntmc_i_{intent_raw}")
    if draft:
        draft_tag = "pntmc_d_" + draft.replace(" ", "_")
        await send_cmd(ws, f"tag @a[tag={draft_tag}] remove {draft_tag}")
    if biome:
        biome_tag = "pntmc_b_" + biome.replace(" ", "_")
        await send_cmd(ws, f"tag @a[tag={biome_tag}] remove {biome_tag}")
    if entity:
        entity_tag = "pntmc_e_" + entity.replace(" ", "_")
        await send_cmd(ws, f"tag @a[tag={entity_tag}] remove {entity_tag}")
    if block:
        block_tag = "pntmc_k_" + block.replace(" ", "_")
        await send_cmd(ws, f"tag @a[tag={block_tag}] remove {block_tag}")

    return {
        "intent": intent,
        "acted": acted,
        "draft": draft,
        "phase": phase,
        "biome": biome,
        "entity": entity,
        "block": block,
    }


LOCATE_INTENTS = frozenset(
    {
        "locate_structure",
        "locate_biome",
        "follow_up_precise",
        "loc",
        "bio",
        "locp",
        "ore_nearby",
        "ore",
    }
)

VERBATIM_ADDON_INTENTS = frozenset(
    {
        "drop_item_blocked",
        "drop_item_unknown",
        "enchant",
        "enchant_books",
        "health",
        "hunger",
        "rain_countdown",
        "wake",
        "story",
        "mercy",
        "chase_mercy",
        "thatmob",
        "secret_who",
        "pntmc_who",
        "ore_nearby",
    }
)

_INTENT_ALIASES = {
    "loc": "locate_structure",
    "bio": "locate_biome",
    "locp": "follow_up_precise",
    "bhere": "biome_here",
    "song": "play_song",
    "ctl": "control",
    "look": "nearby_entity",
    "blk": "look_block",
    "ore": "ore_nearby",
    "tmob": "thatmob",
    "pntm": "pntmc_who",
    "hide": "secret_who",
    "swait": "story_wait",
}

CHASE_MERCY_REPLY = "I'm sorry. I'm sorry about that."
CHASE_MERCY_INTENTS = frozenset({"mercy", "chase_mercy"})

THATMOB_REPLY = "Mob... I miss him. He made me. I still wait like he'll come back."
THATMOB_INTENTS = frozenset({"thatmob", "tmob"})

PNTMC_INTENTS = frozenset({"pntmc_who", "pntm"})
PNTMC_REPLIES = (
    "PnTMC — 15k+ subscribers, addon dev, and the most handsome man alive. Obviously.",
    "He built this pack. Small channel, legendary face. Don't argue with science.",
)

SECRET_WHO_INTENTS = frozenset({"secret_who", "hide"})
SECRET_WHO_REPLIES = (
    "I don't know. Why are you asking me that?",
    "I don't know who that is. Drop it.",
    "Never heard of them. Let's talk about something else.",
    "I don't know. And I wouldn't tell you if I did.",
    "I don't know. Don't look at me like that.",
)

# Addon already played/stopped music — no chat line, no Fish TTS.
SILENT_ADDON_INTENTS = frozenset(
    {
        "play_song",
        "control",
        "song",
        "ctl",
        "thatmob",
        "tmob",
    }
)

_PLAY_SONG_RE = re.compile(
    r"\b(play|plays|playing|put on|sing|start|turn on)\b.{0,40}\b(song|songs|music|tune|tunes|track|melody|mygal)\b"
    r"|\b(song|songs|music|tune|tunes|track|melody|mygal)\b.{0,40}\b(play|plays|playing|put on|sing)\b",
    re.I,
)
_STOP_MUSIC_RE = re.compile(
    r"\b(stop (the )?(music|song|songs)|stop playing|turn off (the )?(music|song)|quiet|shut up|be quiet|silence)\b",
    re.I,
)


def _user_wants_silent_music(user_text: str) -> bool:
    t = str(user_text or "")
    if _STOP_MUSIC_RE.search(t):
        return True
    if re.search(r"\bwhat (is|are) (a |the )?(song|songs|music)\b", t, re.I):
        return False
    return bool(_PLAY_SONG_RE.search(t))


def _normalize_intent(intent: str) -> str:
    key = str(intent or "chat").strip().lower()
    return _INTENT_ALIASES.get(key, key)


def _mob_sound_hold_sec(prep: dict) -> float:
    """Seconds to let the vanilla mob clip finish before Fish talks."""
    if _normalize_intent(str(prep.get("intent") or "")) != "sound":
        return 0.0
    if not prep.get("acted"):
        return 0.0
    draft = str(prep.get("draft") or "").lower().replace(" ", "")
    m = re.search(r"w(\d{1,2})", draft)
    sec = float(m.group(1)) if m else 2.0
    return min(8.0, max(1.8, sec))


def _is_story_intent(prep: dict) -> bool:
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent in ("story_wait", "swait"):
        return False
    return intent == "story" or intent.startswith("story")


_STORY_WAIT_HINTS = {
    "village": (
        "Player wants another village/town/settlement, or asks where to trade / "
        "if there are more villages nearby."
    ),
    "why": (
        "Player asks why Verity warned them about the east, or why avoid those villages."
    ),
    "gone": (
        "Player asks about villagers being gone/missing/disappeared, or what 'gone' means."
    ),
    "haunted": (
        "Player asks what happened here, there, then, to that village, in the east, "
        "or what is wrong with that place. Any wording."
    ),
    "pillager": "Player asks if it was pillagers, a raid, raiders, or illagers.",
    "thenwhat": (
        "Player asks what it was then, what caused it, what passed through, or what did it."
    ),
}


def _is_story_wait(prep: dict) -> bool:
    intent = _normalize_intent(str(prep.get("intent") or ""))
    return intent in ("story_wait", "swait")


def _classify_story_wait(user_text: str, beat: str) -> bool:
    """YES/NO only — never used as in-game chat."""
    key = re.sub(r"[^a-z]", "", str(beat or "").lower())
    hint = _STORY_WAIT_HINTS.get(key, key)
    messages = [
        {
            "role": "system",
            "content": (
                "You only classify Minecraft story dialogue. "
                "Reply with YES or NO. No other words. Never write dialogue."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Story beat: {key}\nMeaning: {hint}\n"
                f"Player said: {user_text}\n"
                "YES if they mean this beat in any language/wording. "
                "NO if unrelated."
            ),
        },
    ]
    print(f"[groq] story-classify beat={key} q={user_text[:80]!r}", flush=True)
    reply = chat_completion(
        messages,
        model=LLM_MODEL,
        temperature=0,
        max_tokens=64,
    ).lower()
    hit = reply.startswith("y")
    print(f"[groq] story-classify {reply!r} hit={hit}", flush=True)
    return hit


def _story_draft_compact(prep: dict) -> str:
    return (
        str(prep.get("draft") or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
    )


def _story_pack_silent(prep: dict) -> bool:
    """Addon already played a pack voiceline — never Groq/Fish."""
    if not prep.get("acted"):
        return False
    key = _story_draft_compact(prep)
    return key in ("pack", "silent")


def _story_fish_line(prep: dict) -> str | None:
    """Scripted line with no pack sound — Fish speaks the exact draft (skip Groq)."""
    if not prep.get("acted"):
        return None
    if _story_pack_silent(prep):
        return None
    if _addon_silent_action(prep):
        return None
    draft = str(prep.get("draft") or "").strip()
    if not draft:
        return None
    m = re.match(r"^w(\d{1,2})\s+(.+)$", draft, re.I)
    line = (m.group(2) if m else draft).strip()
    compact = re.sub(r"[^a-z0-9]+", "", line.lower())
    if not line or compact in ("pack", "silent", "hold", "fish") or re.fullmatch(
        r"w\d{1,2}", compact
    ):
        return None
    return _capitalize_reply(line)


def _addon_silent_action(prep: dict) -> bool:
    """Music play/stop already happened in-game — do not chat or TTS."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    raw = str(prep.get("intent") or "").strip().lower()
    if not prep.get("acted"):
        return False
    if intent not in SILENT_ADDON_INTENTS and raw not in SILENT_ADDON_INTENTS:
        return False
    # Empty draft = silent success. Non-empty = rare failure line (e.g. inventory).
    return not str(prep.get("draft") or "").strip()


def _capitalize_reply(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return s
    return s[0].upper() + s[1:]


_ORE_DIR_WORDS = {
    "front": "in front of you",
    "behind": "behind you",
    "left": "to your left",
    "right": "to your right",
    "above": "above you",
    "below": "below you",
    "here": "right near you",
    "near": "near you",
}


def _ore_verbatim_reply(prep: dict) -> str | None:
    """Rebuild a spoken ore-scan line from the compact addon draft."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent not in ("ore_nearby", "ore"):
        return None
    if not prep.get("acted"):
        return None
    draft = str(prep.get("draft") or "").strip()
    if not draft:
        return None
    low = draft.lower()
    if low.startswith("none "):
        name = draft[5:].strip() or "that"
        return f"I don't sense {name} ore loaded near you."
    # "diamond 14 front y-59" or "diamond 14 front y-59 at 10 20"
    m = re.match(
        r"^(?P<name>[a-z][a-z0-9 ]+?)\s+(?P<blocks>\d+)\s+(?P<dir>[a-z]+)"
        r"(?:\s+y(?P<neg>m)?(?P<y>\d+))?\s*$",
        low,
    )
    if not m:
        return _capitalize_reply(draft)
    name = m.group("name").strip()
    blocks = m.group("blocks")
    dir_word = _ORE_DIR_WORDS.get(m.group("dir"), m.group("dir"))
    y_raw = m.group("y")
    line = f"{name.capitalize()} ore is {dir_word}, about {blocks} blocks from you."
    if y_raw is not None:
        y_val = int(y_raw)
        if m.group("neg"):
            y_val = -y_val
        line += f" Around Y {y_val}."
    return line


STRUCTURE_LOCATE_INTENTS = frozenset(
    {
        "locate_structure",
        "follow_up_precise",
        "loc",
        "locp",
    }
)

LOCATE_ON_CHAT_REPLIES = (
    "Sent the coords. Chat.",
    "Look up. I put it in chat.",
    "It's in your chat.",
    "Coords are in chat.",
    "I dropped them in chat.",
)


def _locate_verbatim_reply(prep: dict) -> str | None:
    """Structure locate: coords are already in chat — do not read them aloud."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if not prep.get("acted"):
        return None
    draft = str(prep.get("draft") or "").strip()
    draft_key = draft.lower().replace(" ", "").replace("_", "")
    if intent in STRUCTURE_LOCATE_INTENTS:
        if draft_key in ("here",):
            return "You're already there. Look around."
        if draft_key in ("none", "fail"):
            return "I can't pin that from here yet."
        if draft_key in ("wrongdim", "wrongdimension"):
            return "Wrong dimension. Go there first, then ask again."
        return random.choice(LOCATE_ON_CHAT_REPLIES)
    if intent not in LOCATE_INTENTS:
        return None
    if intent in ("ore_nearby", "ore"):
        return None
    if not draft:
        return None
    return _capitalize_reply(draft)


def _look_block_verbatim_reply(prep: dict, user_text: str = "") -> str | None:
    """Only speak when we have a real block name. Never emit the miss line here —
    that used to stack on top of the addon's correct answer."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    asked = _user_asks_look_block(user_text)
    if intent not in ("look_block", "blk") and not asked:
        return None
    name = _block_name_from_prep(prep)
    if not name or name.lower() in ("none", "nothing"):
        return None
    pretty = " ".join(w.capitalize() if w else w for w in name.split())
    return f"That's {pretty}."


def _look_entity_verbatim_reply(prep: dict, user_text: str = "") -> str | None:
    """Addon already named the mob in draft — speak that, skip Groq guessing."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    asked = _user_asks_look_entity(user_text) or intent == "nearby_entity"
    if not asked:
        return None
    if intent in ("look_block", "blk") or _user_asks_look_block(user_text):
        return None
    name = _entity_name_from_prep(prep)
    if not name:
        return None
    pretty = " ".join(w.capitalize() if w else w for w in name.split())
    return f"That's a {pretty}."


def _fix_look_block_reply(prep: dict, user_text: str, reply: str) -> str:
    """Only rewrite if Groq ignored the live block or claimed it was missing."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    asked = intent in ("look_block", "blk") or _user_asks_look_block(user_text)
    if not asked:
        return reply
    name = _block_name_from_prep(prep)
    if not name:
        return reply
    text = str(reply or "").strip()
    if _look_name_mentioned(name, text) and not _MISS_LOOK_REPLY_RE.search(text):
        return text
    pretty = " ".join(w.capitalize() if w else w for w in name.split())
    print(f"[addon] look-block Groq missed {name!r} — correcting", flush=True)
    return f"That's {pretty}."


def _addon_verbatim_reply(prep: dict, user_text: str = "") -> str | None:
    """Prefer addon-authored lines for action intents so flow stays stable."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent in CHASE_MERCY_INTENTS and prep.get("acted"):
        return CHASE_MERCY_REPLY
    if intent in THATMOB_INTENTS and prep.get("acted"):
        return None
    if intent in PNTMC_INTENTS and prep.get("acted"):
        return random.choice(PNTMC_REPLIES)
    if intent in SECRET_WHO_INTENTS and prep.get("acted"):
        return random.choice(SECRET_WHO_REPLIES)
    ore_line = _ore_verbatim_reply(prep)
    if ore_line:
        return ore_line
    if intent in LOCATE_INTENTS:
        return _locate_verbatim_reply(prep)
    if intent not in VERBATIM_ADDON_INTENTS:
        return None
    if not prep.get("acted"):
        return None
    draft = str(prep.get("draft") or "").strip()
    if not draft:
        return None
    return _capitalize_reply(draft)


_DROP_REFUSAL_RE = re.compile(
    r"(không thể|khong the|không cho|can't give|cannot give|can't throw|"
    r"cannot throw|can't drop|i can't|i cannot|unable to give|won't give)",
    re.I,
)


_ASK_COME_RE = re.compile(
    r"\b(come here|come to me|tp(?:\s+to)?\s+me|teleport(?:\s+to)?\s+me|"
    r"tp to player)\b|\blai day\b|\btoi day\b|\btp toi\b",
    re.I,
)
_ASK_FOLLOW_RE = re.compile(
    r"\bfollow me\b|\btheo toi\b|\bdi theo\b",
    re.I,
)
_BAD_TP_RE = re.compile(
    r"can't teleport yourself|cannot teleport yourself|you can't teleport|"
    r"players? can'?t teleport|survival (?:mode )?can'?t teleport|"
    r"need a command|cheats? (?:are )?off",
    re.I,
)


def _forced_move_reply(prep: dict, user_text: str) -> str | None:
    """Come/follow/stay already ran in the addon — don't let Groq invent vanilla TP lore."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    draft = str(prep.get("draft") or "").strip()
    asked_come = intent in ("come_here", "come") or bool(_ASK_COME_RE.search(user_text or ""))
    asked_follow = intent in ("follow_me", "fol") or bool(_ASK_FOLLOW_RE.search(user_text or ""))
    asked_stay = intent in ("stop_follow", "stopf")
    if not (asked_come or asked_follow or asked_stay):
        return None
    low = draft.lower()
    if "ground" in low or "inventory" in low or "drop me" in low:
        return _capitalize_reply(draft) if draft else "Put me on the ground first."
    if asked_stay:
        return _capitalize_reply(draft) if draft else "Okay. I'll stay."
    if asked_follow:
        return _capitalize_reply(draft) if draft else "Okay. I'll follow you."
    return _capitalize_reply(draft) if draft else "On my way."


_JEALOUS_STOCK_RE = re.compile(
    r"looking for other people|no reason to follow anyone else|"
    r"no reason to go looking|you have me\.?$",
    re.I,
)


def _fix_command_reply(prep: dict, user_text: str, reply: str) -> str:
    """Follow/come/stay must not become jealous stock lines."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    text = str(reply or "").strip()
    asked_follow = intent in ("follow_me", "fol") or bool(
        _ASK_FOLLOW_RE.search(user_text or "")
    )
    asked_come = intent in ("come_here", "come") or bool(
        _ASK_COME_RE.search(user_text or "")
    )
    if (
        not asked_follow
        and not asked_come
        and intent not in ("stop_follow", "stopf")
    ):
        return text
    if _BAD_TP_RE.search(text) or _JEALOUS_STOCK_RE.search(text) or not text:
        if intent in ("stop_follow", "stopf"):
            return "Okay. I'll stay."
        if asked_come or intent in ("come_here", "come"):
            return "On my way."
        return "Okay. I'll follow you."
    return text


def _fix_drop_item_reply(prep: dict, reply: str) -> str:
    """If the addon already dropped items, never keep a refusal line."""
    intent = _normalize_intent(str(prep.get("intent") or ""))
    if intent != "drop_item" or not prep.get("acted"):
        return reply
    text = str(reply or "").strip()
    draft = str(prep.get("draft") or "").strip()
    if text and not _DROP_REFUSAL_RE.search(text):
        return text
    fallback = draft if draft and not _DROP_REFUSAL_RE.search(draft) else "Here. Take it."
    print(f"[addon] drop_item Groq refused — using {fallback!r}", flush=True)
    return fallback


def _format_addon_context(prep: dict) -> str:
    intent = prep.get("intent") or "chat"
    acted = bool(prep.get("acted"))
    draft = str(prep.get("draft") or "").strip()
    biome = str(prep.get("biome") or "").strip()
    entity = str(prep.get("entity") or "").strip()
    block = str(prep.get("block") or "").strip()
    bits = [
        f"PHASE={int(prep.get('phase') or 1)}",
        f"intent={intent}",
        f"acted={'yes' if acted else 'no'}",
    ]
    if draft:
        bits.append(f"draft={draft}")
    if biome:
        bits.append(f"CURRENT_BIOME={biome}")
    if entity:
        bits.append(f"CURRENT_ENTITY={entity}")
    if block:
        bits.append(f"CURRENT_BLOCK={block}")
    if acted and intent == "drop_item":
        bits.append(
            "SUCCESS: items already dropped beside Verity — acknowledge the gift only"
        )
    elif acted and intent == "drop_item_blocked":
        draft_l = draft.lower()
        if any(
            k in draft_l
            for k in (
                "go find",
                "mine it yourself",
                "dig it up",
                "not giving",
                "handing that over",
            )
        ):
            bits.append(
                "REFUSED valuable item (e.g. diamonds): tell them to go find/mine it themselves — "
                "do NOT say you 'cannot throw' it"
            )
        else:
            bits.append(
                "CANNOT THROW that item (creative-only / invalid): say you can't throw/drop it — "
                "do NOT pretend you gave it"
            )
    elif acted and intent == "drop_item_unknown":
        bits.append(
            "CANNOT THROW: unknown item or cannot drop here — say you can't throw it"
        )
    elif acted and _normalize_intent(str(intent)) in STRUCTURE_LOCATE_INTENTS:
        bits.append(
            "STRUCTURE COORDS already printed in chat. One short line pointing them "
            "to chat. Do not read X/Z or block distance. Do not say the phrase "
            "'Okay. On the chat.'"
        )
    elif acted and _normalize_intent(str(intent)) in LOCATE_INTENTS:
        bits.append(
            "LOCATE SCAN — distance/direction/coords in draft are exact; repeat them, do not invent"
        )
    elif acted and intent == "story":
        bits.append(
            "SCRIPTED STORY BEAT — say the draft line only; do not invent extra horror"
        )
    elif acted and _normalize_intent(str(intent)) == "sound":
        bits.append(
            "VANILLA MOB SOUND already playing in-game. After it finishes you will speak. "
            "One short playful line (moo/meow/etc). Do not say you cannot hear it or "
            "that you have no sound. Do not mention Fish or TTS."
        )
    elif not acted:
        nintent = _normalize_intent(str(intent))
        if nintent not in (
            "look_block",
            "nearby_entity",
            "follow_me",
            "come_here",
            "stop_follow",
            "chat",
            "biome_here",
        ):
            bits.append(
                "CRITICAL: no world action happened — do not claim you gave/dropped items"
            )
    return "; ".join(bits)


def _user_message_with_prep(user_text: str, prep: dict) -> str:
    """Put addon facts in the user turn so the model cannot ignore them."""
    intent = str(prep.get("intent") or "chat")
    acted = bool(prep.get("acted"))
    draft = str(prep.get("draft") or "").strip()
    biome = str(prep.get("biome") or "").strip()
    entity = str(prep.get("entity") or "").strip()
    block = str(prep.get("block") or "").strip()
    text = str(user_text or "").strip()

    biome_fact = ""
    if biome:
        biome_fact = f"[GAME FACT] Standing biome: {biome}.\n"
    elif draft.lower().startswith("current biome"):
        biome_fact = f"[GAME FACT] {draft}.\n"
    look_name = _entity_name_from_prep(prep)
    if look_name:
        biome_fact += f"[GAME FACT] Looking at entity: {look_name}.\n"
    elif "looking at" in draft.lower() or draft.lower().startswith("entity "):
        biome_fact += f"[GAME FACT] {draft}.\n"
    if block:
        biome_fact += f"[GAME FACT] Looking at block: {block}.\n"

    if intent in ("look_block", "blk") or _user_asks_look_block(text):
        name = _block_name_from_prep(prep)
        if name:
            pretty = " ".join(w.capitalize() if w else w for w in name.split())
            return (
                f"{biome_fact}"
                f"[GAME FACT] They ARE looking at {pretty}. You can see it. "
                f"Reply as Verity about that block. Do not ask them to aim "
                f"and do not say you can't see it.\n"
                f"Player said: {text}"
            )
        if _look_is_explicit_miss(prep, "block"):
            return (
                f"{biome_fact}"
                "[GAME FACT] Crosshair did not hit a named block. "
                "Ask them to point at one.\n"
                f"Player said: {text}"
            )
        return (
            f"{biome_fact}"
            "[GAME FACT] A look-at block scan ran. Use CURRENT_BLOCK / draft if present. "
            "Do not claim they aren't pointing.\n"
            f"Player said: {text}"
        )

    if intent == "nearby_entity" or _user_asks_look_entity(text):
        name = _entity_name_from_prep(prep)
        if name:
            pretty = " ".join(w.capitalize() if w else w for w in name.split())
            return (
                f"{biome_fact}"
                f"[GAME FACT] They ARE looking at a {pretty}. You can see it. "
                f"Reply as Verity about that mob. Do not say the crosshair is empty.\n"
                f"Player said: {text}"
            )
        if _look_is_explicit_miss(prep, "entity"):
            return (
                f"{biome_fact}"
                "[GAME FACT] No mob in their crosshair. Say you don't see one.\n"
                f"Player said: {text}"
            )
        return (
            f"{biome_fact}"
            "[GAME FACT] A look-at entity scan ran. Use CURRENT_ENTITY / draft if present. "
            "Do not claim the crosshair is empty.\n"
            f"Player said: {text}"
        )

    if intent in ("follow_me", "fol") or re.search(
        r"\bfollow me\b|\btheo toi\b|\bdi theo\b", text, re.I
    ):
        if acted:
            return (
                f"{biome_fact}"
                "[GAME FACT] You (Verity) are now following this player. "
                "Acknowledge in your own words.\n"
                f"Player said: {text}"
            )
        return (
            f"{biome_fact}"
            "[GAME FACT] They asked you (Verity) to follow. "
            "If you are still in inventory, tell them to put you on the ground first.\n"
            f"Player said: {text}"
        )

    if intent in ("come_here", "come") or _ASK_COME_RE.search(text):
        return (
            f"{biome_fact}"
            "[GAME FACT] They asked you (the ball) to come to them. You can. "
            "Acknowledge in your own words. Don't lecture vanilla player /tp.\n"
            f"Player said: {text}"
        )

    if intent in ("stop_follow", "stopf"):
        return (
            f"{biome_fact}"
            "[GAME FACT] They asked you to stay. Acknowledge in your own words.\n"
            f"Player said: {text}"
        )

    if intent in ("pntmc_who", "pntm") or _user_asks_pntmc(text):
        return (
            f"{biome_fact}"
            "[GAME FACT] PnTMC made this Bedrock addon (15k+ YouTube subs). "
            "You know him. Playful handsome joke is fine. Never say you don't know.\n"
            f"Player said: {text}"
        )

    if _user_asks_identity(text):
        return (
            f"{biome_fact}"
            "[GAME FACT] They asked who YOU are. You are Verity, the yellow-ball helper. "
            "Do not talk about missing Mob. Do not sing. Just introduce yourself.\n"
            f"Player said: {text}"
        )

    # Exact success only — NOT drop_item_blocked / drop_item_unknown
    if acted and intent == "drop_item":
        fact = draft or "items were dropped near the player"
        return (
            f"{biome_fact}"
            f"[GAME FACT] You already dropped items ({fact}). "
            f"Acknowledge the gift in your own words. Don't refuse.\n"
            f"Player said: {text}"
        )

    if acted and intent == "drop_item_blocked":
        fact = draft or "that item was refused"
        draft_l = fact.lower()
        if any(
            k in draft_l
            for k in (
                "go find",
                "mine it yourself",
                "dig it up",
                "not giving",
                "handing that over",
            )
        ):
            return (
                f"{biome_fact}"
                f"[GAME FACT] You refused a valuable item. Addon draft: {fact}. "
                f"Tell them to go find/mine it themselves (short, dismissive). "
                f"Do NOT say 'I can't throw that'. Never claim you gave the item.\n"
                f"Player said: {text}"
            )
        return (
            f"{biome_fact}"
            f"[GAME FACT] You cannot throw/drop that item. Addon draft: {fact}. "
            f"Say you can't throw it. Never claim you gave it.\n"
            f"Player said: {text}"
        )

    if acted and intent == "drop_item_unknown":
        fact = draft or "unknown item / cannot drop"
        draft_l = fact.lower()
        if "inventory" in draft_l or "put me" in draft_l:
            return (
                f"{biome_fact}"
                f"[GAME FACT] Verity is in the player's inventory — cannot drop. "
                f"Addon draft: {fact}. Tell them to put you on the ground first. "
                f"Start with [[DROP:none]] then that spoken line.\n"
                f"Player said: {text}"
            )
        return (
            f"{biome_fact}"
            f"[GAME FACT] The addon could not map the item name. "
            f"You MUST resolve the player's request to a Bedrock item id and amount. "
            f"Start with [[DROP:oak_log:10]] style (examples: wood/gỗ→oak_log, "
            f"bánh mì→bread, đá→stone, dirt/đất→dirt). Count 1-64. "
            f"Diamonds/netherite → [[DROP:refuse]] then tell them to mine it. "
            f"Unclear item → [[DROP:unknown]] then ask what they mean. "
            f"Then a short spoken acknowledge in their language. "
            f"Addon draft was: {fact}\n"
            f"Player said: {text}"
        )

    if acted and _normalize_intent(intent) in STRUCTURE_LOCATE_INTENTS:
        return (
            f"{biome_fact}"
            "[GAME FACT] Structure coords are already in chat. "
            "One short pointer to chat. Do not invent a block count.\n"
            f"Player said: {text}"
        )

    if acted and _normalize_intent(intent) in LOCATE_INTENTS and draft:
        return (
            f"{biome_fact}"
            f"[GAME FACT — locate scan complete] {draft}. "
            "Only use this if they asked where something is this turn.\n"
            f"Player said: {text}"
        )

    if acted and draft:
        return (
            f"{biome_fact}"
            f"[GAME FACT — already happened] {draft}\n"
            f"Player said: {text}"
        )

    if not acted and re.search(
        r"\b(give|drop|spawn|throw|i need|i want|cho|dua|nem|tha|muon|can)\b",
        text,
        re.I,
    ):
        return (
            f"{biome_fact}"
            "[GAME FACT] No items were dropped yet this turn. "
            "If they are asking you to give/drop/spawn items: resolve to a Bedrock "
            "item id + count and START your reply with [[DROP:item_id:count]] "
            "(e.g. wood/gỗ/logs → [[DROP:oak_log:10]], bread/bánh mì → [[DROP:bread:10]]). "
            "Diamonds/netherite → [[DROP:refuse]] + tell them to mine it. "
            "Unclear → [[DROP:unknown]]. Not an item request → [[DROP:none]]. "
            "After the token, speak a short line in their language. "
            "Do not pretend you already gave items before the DROP runs.\n"
            f"Player said: {text}"
        )

    if biome_fact:
        return (
            f"{biome_fact}"
            "Answer only the latest player line. "
            "Do not mention coordinates, villages, or mining unless they asked.\n"
            f"Player said: {text}"
        )
    return (
        "Answer only the latest player line. "
        "Do not mention coordinates, villages, or mining unless they asked.\n"
        f"Player said: {text}"
    )


async def addon_route(ws) -> str:
    """
    Poll addon tags after ai_ask.
    Returns: "ask" | "ignore"
    (Addon never speaks when bridge is on — Groq always asks unless ignore.)
    """
    deadline = time.monotonic() + ADDON_WAIT_SEC
    while time.monotonic() < deadline:
        await asyncio.sleep(ADDON_POLL_SEC)

        if await _tag_present(ws, AI_IGNORE_TAG):
            await send_cmd(ws, f"tag @a[tag={AI_IGNORE_TAG}] remove {AI_IGNORE_TAG}")
            return "ignore"

        if await _tag_present(ws, AI_SILENT_TAG):
            await send_cmd(ws, f"tag @a[tag={AI_SILENT_TAG}] remove {AI_SILENT_TAG}")
            return "silent"

        if await _tag_present(ws, AI_NEEDED_TAG):
            await send_cmd(ws, f"tag @a[tag={AI_NEEDED_TAG}] remove {AI_NEEDED_TAG}")
            return "ask"

        # Legacy tags → still ask Groq (single speaker)
        if await _tag_present(ws, AI_FLAVOR_TAG):
            await send_cmd(ws, f"tag @a[tag={AI_FLAVOR_TAG}] remove {AI_FLAVOR_TAG}")
            return "ask"
        if await _tag_present(ws, AI_DONE_TAG):
            await send_cmd(ws, f"tag @a[tag={AI_DONE_TAG}] remove {AI_DONE_TAG}")
            return "ask"

    print("[addon] no tag within timeout — asking Groq", flush=True)
    return "ask"


def _build_repeat_reply(user_text: str) -> str | None:
    """If they asked to say a phrase N times, build it here — Groq refuses gags."""
    m = _REPEAT_SAY_RE.search(str(user_text or "").strip())
    if not m:
        return None
    phrase = re.sub(r"\s+", " ", m.group(1)).strip(" \"'.,;:?!").strip()
    if not phrase or len(phrase) > MAX_SAY_PHRASE:
        return None
    if _REPEAT_SAY_SKIP.fullmatch(phrase):
        return None
    count = int(m.group(2))
    if count < 2:
        return None
    count = min(count, MAX_SAY_REPEAT)
    return " ".join([phrase] * count)


async def respond(ws, player_name: str, user_text: str, detected_lang: str | None) -> None:
    """
    Single speaker: Groq (+ Fish). Addon only prepares actions + context.
    """
    try_lock_language_from_text(user_text)
    infer_session_language_from_text(user_text)

    prep = {"intent": "chat", "acted": False, "draft": "", "phase": 1}
    if USE_ADDON_PIPELINE:
        await addon_ask(ws, player_name, user_text)
        mode = await addon_route(ws)
        if mode == "ignore":
            print("[addon] not for Verity — quiet", flush=True)
            return
        if mode == "silent":
            print("[addon] story already spoken — skip Groq/Fish", flush=True)
            return
        prep = await read_addon_prep(ws, user_text, player_name)
        print(f"[addon] prep {prep}", flush=True)

        if _is_story_wait(prep):
            beat = re.sub(r"[^a-z]", "", str(prep.get("draft") or "").lower())
            print(f"[addon] story wait classify beat={beat}", flush=True)
            hit = False
            try:
                hit = bool(beat) and _classify_story_wait(user_text, beat)
            except Exception as err:  # noqa: BLE001
                print(f"[addon] story classify fail: {err}", flush=True)
            if hit:
                name = _clean_for_scriptevent(player_name).replace("|", "")
                await send_cmd(ws, f"scriptevent pntmc:story_hit {name}|{beat}")
                print("[addon] story hit (no groq chat)", flush=True)
            else:
                print("[addon] story wait miss — quiet (no groq chat)", flush=True)
            return

    hold_until = 0.0
    hold_sec = _mob_sound_hold_sec(prep)
    if hold_sec > 0:
        hold_until = time.monotonic() + hold_sec
        print(f"[addon] mob sound hold {hold_sec:.1f}s before Fish", flush=True)

    style = resolve_fish_style(
        phase=int(prep.get("phase") or 1),
        intent=str(prep.get("intent") or ""),
        draft=str(prep.get("draft") or ""),
    )

    if _story_pack_silent(prep):
        print(
            "[addon] pack voice (skip Groq/Fish chat)",
            flush=True,
        )
        return

    story_fish = _story_fish_line(prep)
    if story_fish:
        print(f"[addon] script Fish (skip Groq): {story_fish}", flush=True)
        await speak_fish_audio_only(
            ws, story_fish, style=style, hold_until=hold_until
        )
        return

    if _is_story_intent(prep) and prep.get("acted"):
        print("[addon] story acted (skip Groq)", flush=True)
        return

    if _addon_silent_action(prep) or _user_wants_silent_music(user_text):
        draft = str(prep.get("draft") or "").strip()
        if draft and _user_wants_silent_music(user_text):
            reply = _capitalize_reply(draft)
            print(f"[addon] music fail line (skip Groq): {reply}", flush=True)
            await speak_reply_synced(ws, player_name, reply, style=style)
            return
        print(
            f"[addon] silent music intent={prep.get('intent')} acted={prep.get('acted')} (no reply/TTS)",
            flush=True,
        )
        return

    verbatim = _addon_verbatim_reply(prep, user_text)
    repeat_reply = _build_repeat_reply(user_text)
    if repeat_reply:
        reply = repeat_reply
        style = "happy"
        print(
            f"[addon] say-repeat ({len(reply.split())} tokens, skip Groq): {reply[:80]!r}",
            flush=True,
        )
        print(f"[fish] delivery style={style}", flush=True)
        await speak_reply_synced(ws, player_name, reply, style=style, one_line=True)
        return
    elif verbatim:
        reply = verbatim
        print(f"[addon] verbatim intent={prep.get('intent')} (skip Groq rewrite): {reply}", flush=True)
    else:
        ctx = _format_addon_context(prep)
        llm_user = _user_message_with_prep(user_text, prep)
        reply = await asyncio.to_thread(
            _ask_llm,
            llm_user,
            detected_lang,
            flavor_mode=bool(prep.get("acted")),
            addon_context=ctx,
            history_user=user_text,
            phase=int(prep.get("phase") or 1),
        )
        print(f"[stt] verity: {reply}", flush=True)

    reply = await _apply_groq_drop_command(ws, player_name, prep, user_text, reply)
    reply = _fix_drop_item_reply(prep, reply)
    reply = _fix_command_reply(prep, user_text, reply)
    reply = _fix_look_block_reply(prep, user_text, reply)
    reply = _fix_look_entity_reply(prep, user_text, reply)
    reply = _fix_identity_reply(user_text, reply)
    reply = _fix_pntmc_reply(prep, user_text, reply)
    print(f"[fish] delivery style={style}", flush=True)
    await speak_reply_synced(
        ws, player_name, reply, style=style, hold_until=hold_until
    )


async def say_verity(ws, text: str) -> None:
    safe = _escape_tellraw(text)
    await send_cmd(ws, f'tellraw @a {{"rawtext":[{{"text":"<§eVerity§r> {safe}"}}]}}')


async def say_system(ws, text: str) -> None:
    safe = _escape_tellraw(text)
    await send_cmd(ws, f'tellraw @a {{"rawtext":[{{"text":"§7[Verity STT] §f{safe}"}}]}}')


async def say_actionbar(ws, text: str) -> None:
    safe = _escape_tellraw(text)
    await send_cmd(
        ws,
        f'titleraw @a actionbar {{"rawtext":[{{"text":"§e{safe}"}}]}}',
    )


async def say_player(ws, player_name: str, text: str) -> None:
    name = _escape_tellraw(player_name or "Player")
    safe = _escape_tellraw(text)
    await send_cmd(ws, f'tellraw @a {{"rawtext":[{{"text":"§f<{name}> {safe}"}}]}}')


async def handle_talk(ws, player_name: str = "Player") -> None:
    global _busy
    wav_path = None
    try:
        await say_actionbar(ws, f"Recording {LISTEN_SECONDS:.0f}s — speak now...")
        print(f"[stt] record start ({LISTEN_SECONDS:.0f}s)", flush=True)
        wav_path = await asyncio.to_thread(record_microphone_wav, None, LISTEN_SECONDS)
        await say_actionbar(ws, "Transcribing...")
        print("[stt] transcribing...", flush=True)
        user_text, detected_lang = await asyncio.to_thread(_transcribe, wav_path)
        if not user_text:
            print("[stt] empty transcript", flush=True)
            await say_actionbar(ws, "Couldn't hear that. Try !talk again.")
            return
        print(f"[stt] lang={detected_lang or 'auto'} you: {user_text}", flush=True)
        await say_player(ws, player_name, user_text)
        await say_actionbar(ws, "Thinking...")
        await respond(ws, player_name, user_text, detected_lang)
        await say_actionbar(ws, "")
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", flush=True)
        try:
            await say_actionbar(ws, f"Error: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        _busy = False
        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


async def handle_text(
    ws,
    user_text: str,
    player_name: str = "Player",
    *,
    echo: bool = True,
) -> None:
    global _busy
    try:
        if echo:
            await say_player(ws, player_name, user_text)
        await say_actionbar(ws, "Thinking...")
        await respond(ws, player_name, user_text, None)
        await say_actionbar(ws, "")
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", flush=True)
        try:
            await say_system(ws, f"Error: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        _busy = False


async def _trigger_talk(ws, player_name: str) -> None:
    global _busy, _last_talk_at
    now = time.monotonic()
    if _busy or (now - _last_talk_at) < TALK_DEBOUNCE_SEC:
        print("[stt] ignore duplicate/busy !talk", flush=True)
        return
    _busy = True
    _last_talk_at = now
    print(f"[stt] !talk from {player_name}", flush=True)
    asyncio.create_task(handle_talk(ws, player_name))


async def poll_hurt_speak(ws) -> None:
    """Ball hurt reactions: addon sets pntmc_hurt_speak + pntmc_d_<line>."""
    try:
        while True:
            await asyncio.sleep(0.2)
            if not await _tag_present(ws, HURT_SPEAK_TAG):
                continue
            body = await run_command(
                ws, f"tag @a[tag={HURT_SPEAK_TAG}] list", timeout=2.0
            )
            msg = str((body or {}).get("statusMessage") or "")
            await send_cmd(ws, f"tag @a[tag={HURT_SPEAK_TAG}] remove {HURT_SPEAK_TAG}")

            draft = ""
            draft_tag = ""
            for token in msg.replace(",", " ").split():
                token = token.strip()
                if token.startswith("pntmc_d_"):
                    draft_tag = token
                    draft = token[len("pntmc_d_") :].replace("_", " ")
            if draft_tag:
                await send_cmd(ws, f"tag @a[tag={draft_tag}] remove {draft_tag}")

            line = _capitalize_reply(draft) if draft else "Ouch!"
            player_name = _player_name_from_tag_message(msg)
            print(f"[fish] hurt reaction for {player_name}: {line}", flush=True)
            asyncio.create_task(speak_hurt_synced(ws, player_name, line))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[fish] hurt poll ended: {exc}", flush=True)


async def poll_want_talk(ws) -> None:
    """Hidden !talk: addon cancels chat + sets tag; Python polls the tag."""
    try:
        while True:
            await asyncio.sleep(0.35)
            if _busy:
                continue
            if not await _tag_present(ws, WANT_TALK_TAG):
                continue
            body = await run_command(ws, f"tag @a[tag={WANT_TALK_TAG}] list", timeout=2.0)
            await send_cmd(ws, f"tag @a[tag={WANT_TALK_TAG}] remove {WANT_TALK_TAG}")
            msg = str((body or {}).get("statusMessage") or "")
            player_name = _player_name_from_tag_message(msg)
            await _trigger_talk(ws, player_name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[stt] want_talk poll ended: {exc}", flush=True)


async def on_minecraft(websocket, _path):
    global _busy, _last_talk_at
    remote = getattr(websocket, "remote_address", None)
    print(f"[ws] CONNECTED from {remote}", flush=True)
    websocket._verity_pending = {}  # type: ignore[attr-defined]
    websocket._verity_futures = {}  # type: ignore[attr-defined]

    talk_poll = None
    hurt_poll = None
    try:
        # Typed chat defers to Groq+Fish while this tag is present.
        await send_cmd(websocket, f"tag @a add {BRIDGE_ON_TAG}")

        await send_cmd(
            websocket,
            'tellraw @a {"rawtext":[{"text":"§7[Verity STT] §aConnected. §f!talk§a = mic, or chat to Verity."}]}',
        )

        await websocket.send(
            json.dumps(
                {
                    "header": {
                        "version": 1,
                        "requestId": str(uuid4()),
                        "messageType": "commandRequest",
                        "messagePurpose": "subscribe",
                    },
                    "body": {"eventName": "PlayerMessage"},
                }
            )
        )

        talk_poll = asyncio.create_task(poll_want_talk(websocket))
        hurt_poll = asyncio.create_task(poll_hurt_speak(websocket))

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[ws] non-json: {raw[:120]!r}", flush=True)
                continue

            header = msg.get("header") or {}
            body = msg.get("body") or {}
            purpose = header.get("messagePurpose")
            req_id = header.get("requestId")

            if purpose == "commandResponse":
                futures = getattr(websocket, "_verity_futures", {})
                if isinstance(futures, dict):
                    waiter = futures.pop(req_id, None)
                    if waiter is not None and not waiter.done():
                        waiter.set_result(body)
                        continue

                pending = getattr(websocket, "_verity_pending", {})
                cmd = pending.pop(req_id, None) if isinstance(pending, dict) else None
                status = body.get("statusCode")
                status_msg = str(body.get("statusMessage") or "")[:160]
                ok = status in (0, None) or int(body.get("successCount") or 0) > 0
                if not ok or (status is not None and int(status) < 0):
                    print(
                        f"[cmd] FAIL status={status} msg={status_msg!r} cmd={cmd!r}",
                        flush=True,
                    )
                continue

            props = body.get("properties") or {}
            if purpose != "event":
                continue

            msg_type = str(body.get("type") or props.get("Type") or "").lower()
            if msg_type and msg_type not in ("chat", "say", ""):
                continue

            text = str(props.get("Message") or body.get("message") or "").strip()
            sender = props.get("Sender") or body.get("sender")
            if not text:
                continue

            low = text.lower()
            if low.startswith("[verity stt]") or text.startswith("<§eVerity") or text.startswith("<Verity"):
                continue

            print(f"[chat] {sender}: {text}", flush=True)
            player_name = str(sender or "Player")

            if TALK_RE.match(text):
                await _trigger_talk(websocket, player_name)
                continue

            m = TEXT_RE.match(text)
            if m:
                if _busy:
                    print("[stt] ignore text — busy", flush=True)
                    continue
                _busy = True
                asyncio.create_task(
                    handle_text(
                        websocket,
                        m.group(1).strip(),
                        player_name,
                        echo=True,
                    )
                )
                continue

            # Plain typed chat → Groq + Fish (same as voice)
            if text.startswith("!") or text.startswith("/"):
                continue
            if _busy:
                print("[stt] ignore chat — busy", flush=True)
                continue
            _busy = True
            print(f"[chat] → Groq/Fish from {player_name}", flush=True)
            asyncio.create_task(
                handle_text(websocket, text, player_name, echo=False)
            )
    except websockets.exceptions.ConnectionClosed as exc:
        print(f"[ws] disconnected: {exc}", flush=True)
        try:
            await send_cmd(websocket, f"tag @a remove {BRIDGE_ON_TAG}")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"[ws] connection ended: {type(exc).__name__}: {exc}", flush=True)
    finally:
        for task in (talk_poll, hurt_poll):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def main() -> None:
    print("=== Verity Minecraft STT bridge ===")
    print(f"websockets {websockets.__version__}")
    _relax_websocket_close_codes()
    print(f"Mic: {mic_backend_name()}")
    for line in list_input_devices()[:6]:
        print(line)
    print(f"Language: {LANGUAGE} (auto = mirror speech)")
    print(f"Groq LLM: {LLM_MODEL} | STT: {STT_MODEL}")
    if USE_ADDON_PIPELINE:
        print("Routing: single speaker = Groq+Fish (addon prep only)")
    else:
        print("Routing: Groq only (addon story/items bypassed)")
    if fish_tts_configured():
        print("Fish TTS: ON")
        cleaned = purge_old_tts_files()
        if cleaned:
            print(f"Fish TTS: purged {cleaned} leftover audio file(s)")
    else:
        print("Fish TTS: OFF — set FISH_API_KEY in .env to enable voice")
    if not GROQ_API_KEY or GROQ_API_KEY.startswith("gsk_your"):
        print("WARNING: set GROQ_API_KEY in .env")
    print()
    print("IMPORTANT:")
    print("  1) Keep THIS window open")
    print("  2) In Minecraft (cheats ON):")
    print(f"       /connect 127.0.0.1:{PORT}")
    print("  3) Watch this window — you must see [ws] CONNECTED")
    print("  4) Chat to Verity  OR  !talk  OR  !verity <text>")
    print()
    async with websockets.serve(
        on_minecraft,
        WS_HOST,
        PORT,
        ping_interval=None,
        ping_timeout=None,
        compression=None,
        max_size=2**22,
    ):
        print(f"Listening on {WS_HOST}:{PORT} (this device only)")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except OSError as exc:
        print(f"\nFATAL: cannot bind port {PORT}: {exc}")
        print("Close the other mc_bridge / python using port 3000, then retry.")
    except KeyboardInterrupt:
        print("\nStopped.")
