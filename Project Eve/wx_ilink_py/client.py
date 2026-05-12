from __future__ import annotations

import base64
import io
import json
import os
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional
from ai.chat import evedb

import qrcode
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from markupsafe import escape


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
DEFAULT_API_TIMEOUT = 15
DEFAULT_LONG_POLL_TIMEOUT = 35
QR_POLL_TIMEOUT = 35
LOGIN_TIMEOUT = 8 * 60
MAX_QR_REFRESH = 3
IMAGE_TTL_SECONDS = 24 * 3600  # 图片保留 24 小时
IMAGE_CLEANUP_INTERVAL = 3600  # 每小时清理一次
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY = 30  # 连续失败后退避秒数
RETRY_DELAY = 2


class MessageType:
    USER = 1
    BOT = 2


class MessageItemType:
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState:
    NEW = 0
    GENERATING = 1
    FINISH = 2


class TypingStatus:
    TYPING = 1
    CANCEL = 2


@dataclass
class LoginCredentials:
    token: str
    base_url: str
    account_id: str
    user_id: Optional[str] = None


@dataclass
class QRState:
    qrcode: str = ""
    qrcode_img_content: str = ""
    status: str = "idle"
    updated_at: float = 0.0
    expires_refresh_count: int = 0


@dataclass
class SystemState:
    logged_in: bool = False
    account_id: str = ""
    user_id: str = ""
    base_url: str = DEFAULT_BASE_URL
    last_error: str = ""
    login_mode: str = "saved_credentials"
    last_poll_at: float = 0.0
    total_received: int = 0
    total_sent: int = 0
    queue_size: int = 0
    qr_state: QRState = field(default_factory=QRState)


class ILinkClient:
    def __init__(
        self,
        data_dir: str | os.PathLike[str] = "./data",
        base_url: str = DEFAULT_BASE_URL,
        auto_login: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.credentials_path = self.data_dir / "credentials.json"
        self.images_dir = self.data_dir / "files"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._sync_buf_path = self.data_dir / "sync_buf.json"
        self.session = requests.Session()
        self._lock = threading.RLock()
        self._poll_buf = self._load_sync_buf()
        self._queue: Deque[dict[str, Any]] = deque()
        self._context_tokens: Dict[str, str] = {}
        self._typing_tickets: Dict[str, str] = {}
        self.state = SystemState(base_url=self.base_url)
        self.state.total_received = evedb.get_stat("received_count")
        self.state.total_sent = evedb.get_stat("sent_count")

        self.credentials: Optional[LoginCredentials] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._login_thread: Optional[threading.Thread] = None
        self._start_image_cleanup_scheduler()
        if auto_login:
            self.ensure_login()

    def ensure_login(self, force_refresh: bool = False) -> LoginCredentials:
        with self._lock:
            if force_refresh:
                self.clear_credentials()
            if self.credentials:
                return self.credentials
            saved = self._load_credentials()
            if saved:
                self.credentials = saved
                self._set_logged_in(saved, login_mode="saved_credentials")
                return saved
        return self.login_via_qrcode()

    def start_login_async(self, force_refresh: bool = False) -> None:
        with self._lock:
            if self.state.logged_in:
                return
            if self._login_thread and self._login_thread.is_alive():
                return
            self.state.login_mode = "logging_in"
            self.state.last_error = ""

        def _worker() -> None:
            try:
                self.ensure_login(force_refresh=force_refresh)
            except Exception as exc:
                with self._lock:
                    self.state.last_error = str(exc)
                    if not self.state.logged_in:
                        self.state.login_mode = "login_failed"

        self._login_thread = threading.Thread(target=_worker, name="wx-ilink-login", daemon=True)
        self._login_thread.start()

    def login_via_qrcode(self) -> LoginCredentials:
        refresh_count = 0
        deadline = time.time() + LOGIN_TIMEOUT
        qr = self._fetch_qrcode()
        self._update_qr_state(qr, status="wait", refresh_count=refresh_count)
        current_poll_base_url = self.base_url  # 可能因 IDC redirect 而变更
        while time.time() < deadline:
            try:
                status = self._poll_qrcode_status(qr["qrcode"], current_poll_base_url)
            except Exception:
                # 网络超时/网关错误，视为 wait 继续重试
                time.sleep(1)
                continue
            current_status = status.get("status", "wait")
            if current_status == "wait" or current_status == "scaned":
                self._update_qr_state(qr, status=current_status, refresh_count=refresh_count)
                time.sleep(1)
                continue
            if current_status == "scaned_but_redirect":
                # IDC 重定向：切换后续 poll 的 base URL
                redirect_host = status.get("redirect_host")
                if redirect_host:
                    current_poll_base_url = f"https://{redirect_host}"
                time.sleep(1)
                continue
            if current_status == "expired":
                refresh_count += 1
                if refresh_count >= MAX_QR_REFRESH:
                    raise RuntimeError("二维码多次过期，请重试")
                qr = self._fetch_qrcode()
                self._update_qr_state(qr, status="wait", refresh_count=refresh_count)
                current_poll_base_url = self.base_url  # 刷新后重置 poll URL
                time.sleep(1)
                continue
            self._update_qr_state(qr, status=current_status, refresh_count=refresh_count)
            if current_status == "confirmed":
                token = status.get("bot_token")
                account_id = status.get("ilink_bot_id")
                if not token or not account_id:
                    raise RuntimeError("登录确认但未返回 token 或 bot_id")
                creds = LoginCredentials(
                    token=token,
                    base_url=status.get("baseurl") or self.base_url,
                    account_id=account_id,
                    user_id=status.get("ilink_user_id"),
                )
                self._save_credentials(creds)
                with self._lock:
                    self.credentials = creds
                self._set_logged_in(creds, login_mode="qrcode")
                return creds
            time.sleep(1)
        raise RuntimeError("登录超时")

    def clear_credentials(self) -> None:
        with self._lock:
            self.credentials = None
            self._poll_buf = ""
            self._context_tokens.clear()
            self.state.logged_in = False
            if self.credentials_path.exists():
                self.credentials_path.unlink()
            if self._sync_buf_path.exists():
                self._sync_buf_path.unlink()

    def fetch_messages(self, timeout: int = DEFAULT_LONG_POLL_TIMEOUT) -> tuple[List[dict[str, Any]], int]:
        """返回 (messages, next_timeout_seconds)。"""
        creds = self.ensure_login()
        payload = {
            "get_updates_buf": self._poll_buf,
            "base_info": {"channel_version": "python-client"},
        }
        try:
            data = self._api_post(
                creds.base_url,
                "ilink/bot/getupdates",
                payload,
                token=creds.token,
                timeout=timeout + 5,  # requests 超时略大于 long-poll，给服务端留余量
            )
        except requests.Timeout:
            return [], timeout
        except requests.RequestException as exc:
            print(f"[ILinkClient] getupdates temporary network error: {exc}")
            return [], min(max(timeout, RETRY_DELAY), DEFAULT_LONG_POLL_TIMEOUT)
        if data.get("ret") not in (None, 0) or data.get("errcode") not in (None, 0):
            raise RuntimeError(
                f"getUpdates失败: ret={data.get('ret')} errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            )
        messages = data.get("msgs") or []
        next_buf = data.get("get_updates_buf")
        # 服务端可动态下发下次 poll 超时
        next_timeout = timeout
        if data.get("longpolling_timeout_ms"):
            next_timeout = max(1, int(data["longpolling_timeout_ms"]) // 1000)
        with self._lock:
            if next_buf:
                self._poll_buf = next_buf
                self._save_sync_buf(next_buf)
            self.state.last_poll_at = time.time()
            evedb.increment_received_count()
        
        normalized = []
        for msg in messages:
            normalized_msg = self._normalize_message(msg)
            if normalized_msg.get("from_user_id") and msg.get("context_token"):
                self._context_tokens[normalized_msg["from_user_id"]] = msg["context_token"]
            normalized.append(normalized_msg)
            self._queue.append(normalized_msg)
        with self._lock:
            self.state.queue_size = len(self._queue)
        return normalized, next_timeout

    def get_pending_messages(self, max_count: Optional[int] = None) -> List[dict[str, Any]]:
        with self._lock:
            if max_count is None:
                items = list(self._queue)
                self._queue.clear()
            else:
                items = []
                for _ in range(min(max_count, len(self._queue))):
                    items.append(self._queue.popleft())
            self.state.queue_size = len(self._queue)
            return items

    def start_polling(self, interval: float = 0.0, timeout: int = DEFAULT_LONG_POLL_TIMEOUT) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()

        def _worker() -> None:
            next_timeout = timeout
            consecutive_failures = 0
            while not self._poll_stop.is_set():
                try:
                    _, next_timeout = self.fetch_messages(timeout=next_timeout)
                    consecutive_failures = 0
                except Exception as exc:
                    with self._lock:
                        self.state.last_error = str(exc)
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                        self._poll_stop.wait(BACKOFF_DELAY)
                    else:
                        self._poll_stop.wait(RETRY_DELAY)
                    continue
                if interval > 0:
                    time.sleep(interval)

        self._poll_thread = threading.Thread(target=_worker, name="wx-ilink-poll", daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)

    def send_text(self, text: str, context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        item = {"type": MessageItemType.TEXT, "text_item": {"text": text}}
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_message(target_user_id, [item], context_token=context_token)

    def send_image(self, image_path: str, context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_media_via_cdn(target_user_id, image_path, MessageItemType.IMAGE, context_token)

    def send_voice(self, voice_path: str, context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_media_via_cdn(target_user_id, voice_path, MessageItemType.VOICE, context_token)

    def send_file(self, file_path: str, context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_media_via_cdn(target_user_id, file_path, MessageItemType.FILE, context_token)

    def send_video(self, video_path: str, context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_media_via_cdn(target_user_id, video_path, MessageItemType.VIDEO, context_token)

    def send_items(self, items: Iterable[dict[str, Any]], context_token: Optional[str] = None, to_user_id: Optional[str] = None) -> dict[str, Any]:
        target_user_id = to_user_id or self._get_target_user_id()
        return self._send_message(target_user_id, list(items), context_token=context_token)

    def get_config(self, to_user_id: Optional[str] = None, context_token: Optional[str] = None) -> dict[str, Any]:
        """获取账号配置（包含 typing_ticket）并缓存。"""
        creds = self.ensure_login()
        target_user_id = to_user_id or self._get_target_user_id()
        context_token = context_token or self._context_tokens.get(target_user_id)
        payload = {
            "ilink_user_id": target_user_id,
            "context_token": context_token,
            "base_info": {"channel_version": "python-client"},
        }
        data = self._api_post(creds.base_url, "ilink/bot/getconfig", payload, token=creds.token)
        typing_ticket = data.get("typing_ticket", "")
        with self._lock:
            if typing_ticket:
                self._typing_tickets[target_user_id] = typing_ticket
        return data

    def send_typing(
        self,
        status: int = TypingStatus.TYPING,
        to_user_id: Optional[str] = None,
        typing_ticket: Optional[str] = None,
        context_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """发送正在输入/取消正在输入的状态指示。

        status: TypingStatus.TYPING (1) = 正在输入，TypingStatus.CANCEL (2) = 取消输入。
        typing_ticket: 若未提供则自动从缓存获取；缓存无值时先调用 get_config。
        """
        creds = self.ensure_login()
        target_user_id = to_user_id or self._get_target_user_id()
        context_token = context_token or self._context_tokens.get(target_user_id)

        # 获取 typing_ticket
        ticket = typing_ticket
        if not ticket:
            with self._lock:
                ticket = self._typing_tickets.get(target_user_id, "")
        if not ticket:
            # 缓存中没有，先拉一次配置
            config_data = self.get_config(to_user_id=target_user_id, context_token=context_token)
            ticket = config_data.get("typing_ticket", "")
            if not ticket:
                raise RuntimeError("无法获取 typing_ticket，无法发送输入状态")

        payload = {
            "ilink_user_id": target_user_id,
            "typing_ticket": ticket,
            "status": status,
            "context_token": context_token,
            "base_info": {"channel_version": "python-client"},
        }
        return self._api_post(creds.base_url, "ilink/bot/sendtyping", payload, token=creds.token)

    def cancel_typing(
        self,
        to_user_id: Optional[str] = None,
        typing_ticket: Optional[str] = None,
        context_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """取消正在输入的状态指示。"""
        return self.send_typing(
            status=TypingStatus.CANCEL,
            to_user_id=to_user_id,
            typing_ticket=typing_ticket,
            context_token=context_token,
        )

    def send_text_simulated(
        self,
        text: str,
        context_token: Optional[str] = None,
        to_user_id: Optional[str] = None,
        min_delay: float = 1.0,
        max_delay: float = 20.0,
        keepalive_interval: float = 5.0,
    ) -> dict[str, Any]:
        """模拟人类输入节奏发送文本：先发送「正在输入」，等待一段模拟打字延迟（每隔
        keepalive_interval 秒续发一次正在输入以维持状态），最后取消输入并发送消息。

        text: 要发送的字符串。
        min_delay / max_delay: 模拟打字等待的秒数范围，实际延迟按文本长度在范围内线性映射。
        keepalive_interval: 正在输入续发间隔（秒），与 npm 模块的 keepaliveIntervalMs=5000 对应。
        """
        import random as _random

        target_user_id = to_user_id or self._get_target_user_id()

        # 计算模拟延迟：文本越长延迟越大，但不超出 [min_delay, max_delay]
        char_count = len(text)
        # 简单线性映射：每 10 个字符增加 0.3 秒，上限 max_delay
        delay = min(min_delay + char_count * 0.3, max_delay)
        # 加一点随机抖动，更像人类
        delay = _random.uniform(min_delay, delay)

        # 1. 发送「正在输入」
        self.send_typing(
            status=TypingStatus.TYPING,
            to_user_id=target_user_id,
            context_token=context_token,
        )

        # 2. 等待模拟打字时间，期间每隔 keepalive_interval 续发一次正在输入
        elapsed = 0.0
        while elapsed < delay:
            sleep_time = min(keepalive_interval, delay - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time
            if elapsed < delay:
                # 续发正在输入
                self.send_typing(
                    status=TypingStatus.TYPING,
                    to_user_id=target_user_id,
                    context_token=context_token,
                )

        # 3. 取消正在输入
        self.cancel_typing(
            to_user_id=target_user_id,
            context_token=context_token,
        )

        # 4. 发送实际文本
        return self.send_text(text, context_token=context_token, to_user_id=target_user_id)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self.state)
            qr_state = data.get("qr_state", {})
            qr_image = qr_state.get("qrcode_img_content") or ""
            qr_token = qr_state.get("qrcode") or ""
            qr_url = qr_image if isinstance(qr_image, str) and qr_image.startswith(("http://", "https://")) else ""
            if qr_url:
                qr_image = self._make_qr_data_url(qr_url)
            elif qr_image and not qr_image.startswith(("data:", "http://", "https://")):
                qr_image = f"data:image/png;base64,{qr_image}"
            qr_state["qrcode_img_content"] = qr_image
            qr_state["qrcode_url"] = qr_url
            qr_state["qrcode_token"] = qr_token
            data["qr_state"] = qr_state
            data["credentials_path"] = str(self.credentials_path)
            self.state.total_received = evedb.get_stat("received_count")
            self.state.total_sent = evedb.get_stat("sent_count")
            return data

    def _send_media_via_cdn(
        self,
        to_user_id: str,
        file_path: str,
        item_type: int,
        context_token: Optional[str] = None,
    ) -> dict[str, Any]:
        import hashlib as _hashlib
        creds = self.ensure_login()
        path_obj = Path(file_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        plaintext = path_obj.read_bytes()

        # 语音：先将 WAV 转为 PCM 再 encode 成腾讯 SILK 格式（微信要求）
        voice_playtime_ms = 0
        if item_type == MessageItemType.VOICE:
            try:
                import pilk
                import wave as _wave
                import tempfile, os as _os
                # 读取 WAV 采样率和时长
                with _wave.open(file_path, 'rb') as wf:
                    pcm_rate = wf.getframerate()
                    n_frames = wf.getnframes()
                    voice_playtime_ms = int(n_frames / pcm_rate * 1000)
                tmp_pcm = tempfile.mktemp(suffix='.pcm')
                tmp_silk = tempfile.mktemp(suffix='.silk')
                try:
                    with _wave.open(file_path, 'rb') as wf:
                        with open(tmp_pcm, 'wb') as pf:
                            pf.write(wf.readframes(wf.getnframes()))
                    pilk.encode(tmp_pcm, tmp_silk, pcm_rate=pcm_rate, tencent=True)
                    plaintext = Path(tmp_silk).read_bytes()
                    try:
                        voice_playtime_ms = pilk.get_duration(tmp_silk)
                    except Exception:
                        pass
                finally:
                    for p in (tmp_pcm, tmp_silk):
                        if _os.path.exists(p):
                            _os.unlink(p)
            except Exception as e:
                print(f"WAV→SILK 转换失败，使用原始文件: {e}")

        rawsize = len(plaintext)
        rawfilemd5 = _hashlib.md5(plaintext).hexdigest()
        # AES-128-ECB ciphertext size: PKCS7 padding to 16-byte boundary
        filesize = ((rawsize + 16) // 16) * 16
        filekey = secrets.token_hex(16)
        aeskey_bytes = secrets.token_bytes(16)  # 16 raw bytes

        # UploadMediaType: IMAGE=1, VIDEO=2, FILE=3, VOICE=4
        media_type_map = {
            MessageItemType.IMAGE: 1,
            MessageItemType.VIDEO: 2,
            MessageItemType.FILE: 3,
            MessageItemType.VOICE: 4,
        }
        media_type = media_type_map.get(item_type, 3)

        # Step 1: get CDN upload URL
        upload_req = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_bytes.hex(),
            "base_info": {"channel_version": "python-client"},
        }
        upload_resp = self._api_post(creds.base_url, "ilink/bot/getuploadurl", upload_req, token=creds.token)
        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param")
        if not upload_full_url and not upload_param:
            raise RuntimeError(f"getuploadurl 未返回上传地址: {upload_resp}")

        # Step 2: AES-128-ECB encrypt and upload to CDN
        cipher = AES.new(aeskey_bytes, AES.MODE_ECB)
        from Crypto.Util.Padding import pad as _pad
        ciphertext = cipher.encrypt(_pad(plaintext, AES.block_size))

        cdn_url = upload_full_url
        if not cdn_url and upload_param:
            from urllib.parse import quote
            cdn_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"

        cdn_resp = self.session.post(
            cdn_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60,
        )
        cdn_resp.raise_for_status()
        download_param = cdn_resp.headers.get("x-encrypted-param", "")
        if not download_param:
            raise RuntimeError("CDN 上传响应缺少 x-encrypted-param header")

        # Step 3: build message item with CDN reference
        # aes_key 的编码方式：base64(hex_string) 而不是 base64(raw_bytes)
        # 即先将 16 raw bytes 转 hex 字符串（32字节 ASCII），再 base64 编码
        # 这与接收端 parseAesKey 的 case 2 对应
        aeskey_hex_str = aeskey_bytes.hex()  # 32 char hex string
        aeskey_b64 = base64.b64encode(aeskey_hex_str.encode("ascii")).decode("utf-8")
        cdn_media = {
            "encrypt_query_param": download_param,
            "aes_key": aeskey_b64,
            "encrypt_type": 1,
        }

        if item_type == MessageItemType.IMAGE:
            media_item = {"type": item_type, "image_item": {"media": cdn_media, "mid_size": filesize}}
        elif item_type == MessageItemType.VIDEO:
            media_item = {"type": item_type, "video_item": {"media": cdn_media, "video_size": filesize}}
        elif item_type == MessageItemType.FILE:
            media_item = {
                "type": item_type,
                "file_item": {
                    "media": cdn_media,
                    "file_name": path_obj.name,
                    "len": str(rawsize),
                },
            }
        elif item_type == MessageItemType.VOICE:
            media_item = {
                "type": item_type,
                "voice_item": {
                    "media": cdn_media,
                    "encode_type": 4,       # 4 = WeChat SILK convention (matches WeChat client)
                    "bits_per_sample": 16,
                    "sample_rate": 16000,   # WeChat client convention (actual SILK encoding may differ)
                    "playtime": voice_playtime_ms,
                },
            }
        else:
            raise ValueError(f"不支持的媒体类型: {item_type}")

        return self._send_message(to_user_id, [media_item], context_token=context_token)

    def _send_media_path(
        self,
        to_user_id: str,
        file_path: str,
        item_type: int,
        context_token: Optional[str] = None,
    ) -> dict[str, Any]:
        # 保留兼容旧调用，内部转发到正确的 CDN 上传流程
        return self._send_media_via_cdn(to_user_id, file_path, item_type, context_token)

    def _send_message(
        self,
        to_user_id: str,
        items: List[dict[str, Any]],
        context_token: Optional[str] = None,
    ) -> dict[str, Any]:
        creds = self.ensure_login()
        client_id = f"pybot-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        context_token = context_token or self._context_tokens.get(to_user_id)
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT,
                "message_state": MessageState.FINISH,
                "item_list": items or None,
                "context_token": context_token,
            }
        }
        data = self._api_post(creds.base_url, "ilink/bot/sendmessage", body, token=creds.token)
        evedb.increment_sent_count()
        return data

    def _normalize_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {
            "seq": msg.get("seq"),
            "message_id": msg.get("message_id"),
            "from_user_id": msg.get("from_user_id"),
            "to_user_id": msg.get("to_user_id"),
            "session_id": msg.get("session_id"),
            "message_type": msg.get("message_type"),
            "message_state": msg.get("message_state"),
            "create_time_ms": msg.get("create_time_ms"),
            "context_token": msg.get("context_token"),
            "text": self.extract_text(msg),
            "raw": msg,
        }

    @staticmethod
    def extract_text(msg: dict[str, Any]) -> str:
        for item in msg.get("item_list") or []:
            if item.get("type") != MessageItemType.TEXT:
                continue
            text = ((item.get("text_item") or {}).get("text")) or ""
            ref_msg = item.get("ref_msg") or {}
            title = ref_msg.get("title")
            if title:
                return f"[引用: {title}]\n{text}"
            return text
        return ""

    def _set_logged_in(self, creds: LoginCredentials, login_mode: str) -> None:
        with self._lock:
            self.state.logged_in = True
            self.state.account_id = creds.account_id
            self.state.user_id = creds.user_id or ""
            self.state.base_url = creds.base_url
            self.state.login_mode = login_mode
            self.state.last_error = ""

    def _update_qr_state(self, qr: dict[str, Any], status: str, refresh_count: int) -> None:
        with self._lock:
            self.state.qr_state = QRState(
                qrcode=qr.get("qrcode", ""),
                qrcode_img_content=qr.get("qrcode_img_content", ""),
                status=status,
                updated_at=time.time(),
                expires_refresh_count=refresh_count,
            )

    def _api_post(
        self,
        base_url: str,
        endpoint: str,
        body: dict[str, Any],
        token: Optional[str] = None,
        timeout: int = DEFAULT_API_TIMEOUT,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = self._build_headers(token)
        try:
            response = self.session.post(url, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            with self._lock:
                self.state.last_error = str(exc)
            raise

    def _build_headers(self, token: Optional[str]) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": self._random_wechat_uin(),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _random_wechat_uin() -> str:
        raw_number = secrets.randbits(32)
        return base64.b64encode(str(raw_number).encode("utf-8")).decode("utf-8")

    @staticmethod
    def _make_qr_data_url(content: str) -> str:
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(content)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    def _fetch_qrcode(self) -> dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}"
        response = self.session.get(url, timeout=DEFAULT_API_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _poll_qrcode_status(self, qrcode_str: str, poll_base_url: Optional[str] = None) -> dict[str, Any]:
        from urllib.parse import quote
        base = (poll_base_url or self.base_url).rstrip("/")
        url = f"{base}/ilink/bot/get_qrcode_status?qrcode={quote(qrcode_str)}"
        response = self.session.get(
            url,
            headers={"iLink-App-ClientVersion": "1"},
            timeout=QR_POLL_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def _save_credentials(self, creds: LoginCredentials) -> None:
        self.credentials_path.write_text(json.dumps(asdict(creds), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.credentials_path, 0o600)
        except OSError:
            pass

    def _load_sync_buf(self) -> str:
        try:
            data = json.loads(self._sync_buf_path.read_text(encoding="utf-8"))
            return data.get("get_updates_buf", "")
        except Exception:
            return ""

    def _save_sync_buf(self, buf: str) -> None:
        try:
            self._sync_buf_path.write_text(
                json.dumps({"get_updates_buf": buf}), encoding="utf-8"
            )
        except OSError:
            pass

    def _get_target_user_id(self) -> str:
        """从 credentials 中获取目标用户 ID"""
        if not self.credentials or not self.credentials.user_id:
            raise RuntimeError("未找到目标用户 ID，请先登录")
        return self.credentials.user_id

    def download_media(self, item: dict[str, Any]) -> Path:
        """下载并解密任意类型的媒体消息 item（type 2/3/4/5），保存到 data/media/ 目录。

        item 是消息 item_list 中的单个条目（包含 type 和对应的 *_item 字段）。
        返回本地文件路径。

        类型映射：
          2 (IMAGE)  → .jpg，key 来自 image_item.aeskey (hex) 或 image_item.media.aes_key (b64)
          3 (VOICE)  → .silk，key 来自 voice_item.media.aes_key (b64)
          4 (FILE)   → 原始文件名，key 来自 file_item.media.aes_key (b64)
          5 (VIDEO)  → .mp4，key 来自 video_item.media.aes_key (b64)
        """
        item_type = item.get("type")

        if item_type == MessageItemType.IMAGE:
            sub = item.get("image_item") or {}
            aeskey_hex: str = sub.get("aeskey", "")
            media: dict[str, Any] = sub.get("media") or {}
            ext = ".jpg"
        elif item_type == MessageItemType.VOICE:
            sub = item.get("voice_item") or {}
            aeskey_hex = ""
            media = sub.get("media") or {}
            ext = ".silk"  # 原始格式，下面会转 PCM WAV
        elif item_type == MessageItemType.FILE:
            sub = item.get("file_item") or {}
            aeskey_hex = ""
            media = sub.get("media") or {}
            ext = Path(sub.get("file_name") or "file.bin").suffix or ".bin"
        elif item_type == MessageItemType.VIDEO:
            sub = item.get("video_item") or {}
            aeskey_hex = ""
            media = sub.get("media") or {}
            ext = ".mp4"
        else:
            raise ValueError(f"不支持的媒体类型: {item_type}")

        full_url: str = media.get("full_url", "")
        if not full_url:
            raise ValueError(f"media.full_url 为空，无法下载（type={item_type}）")

        # 解析 AES key
        if aeskey_hex:
            key = bytes.fromhex(aeskey_hex)
        else:
            aes_key_b64: str = media.get("aes_key", "")
            if not aes_key_b64:
                raise ValueError("media.aes_key 为空")
            decoded = base64.b64decode(aes_key_b64)
            if len(decoded) == 16:
                key = decoded
            elif len(decoded) == 32:
                key = bytes.fromhex(decoded.decode("ascii"))
            else:
                raise ValueError(f"aes_key 解码后长度异常: {len(decoded)}")

        # 下载
        resp = self.session.get(full_url, timeout=DEFAULT_API_TIMEOUT)
        resp.raise_for_status()
        encrypted_data = resp.content

        # AES-128-ECB 解密
        cipher = AES.new(key, AES.MODE_ECB)
        try:
            decrypted = unpad(cipher.decrypt(encrypted_data), AES.block_size)
        except Exception:
            cipher2 = AES.new(key, AES.MODE_ECB)
            decrypted = cipher2.decrypt(encrypted_data)

        ts = time.strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:12]

        # 语音：SILK → PCM WAV（24000 Hz mono 16-bit），与原始 npm 模块 silkToWav 一致
        if item_type == MessageItemType.VOICE:
            try:
                import pilk
                import tempfile, os as _os
                with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as tmp:
                    tmp.write(decrypted)
                    tmp_silk_path = tmp.name
                tmp_wav_path = tmp_silk_path + ".wav"
                try:
                    pilk.silk_to_wav(tmp_silk_path, tmp_wav_path, rate=24000)
                    decrypted = Path(tmp_wav_path).read_bytes()
                    ext = ".wav"
                finally:
                    _os.unlink(tmp_silk_path)
                    if _os.path.exists(tmp_wav_path):
                        _os.unlink(tmp_wav_path)
            except Exception as e:
                print(f"SILK 转 WAV 失败，保留原始 SILK: {e}")
                ext = ".silk"

        filename = f"{ts}_{unique_id}{ext}"
        save_path = self.images_dir / filename
        save_path.write_bytes(decrypted)
        return save_path

    def download_image(self, image_item: dict[str, Any]) -> Path:
        """兼容旧接口：下载图片。推荐使用 download_media(item) 代替。"""
        return self.download_media({"type": MessageItemType.IMAGE, "image_item": image_item})

    def _start_image_cleanup_scheduler(self) -> None:
        """启动后台线程，定期清理超过 24 小时的图片。"""
        def _cleanup_loop() -> None:
            while True:
                time.sleep(IMAGE_CLEANUP_INTERVAL)
                self._cleanup_old_images()

        t = threading.Thread(target=_cleanup_loop, name="wx-ilink-img-cleanup", daemon=True)
        t.start()

    def _cleanup_old_images(self) -> None:
        now = time.time()
        for f in self.images_dir.iterdir():
            if not f.is_file():
                continue
            try:
                if now - f.stat().st_mtime > IMAGE_TTL_SECONDS:
                    f.unlink()
            except OSError:
                pass

    def _load_credentials(self) -> Optional[LoginCredentials]:
        if not self.credentials_path.exists():
            return None
        try:
            data = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        token = data.get("token")
        base_url = data.get("base_url") or data.get("baseUrl")
        account_id = data.get("account_id") or data.get("accountId")
        if not token or not base_url or not account_id:
            return None
        return LoginCredentials(
            token=token,
            base_url=base_url,
            account_id=account_id,
            user_id=data.get("user_id") or data.get("userId"),
        )
