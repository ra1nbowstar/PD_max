# -*- coding: utf-8 -*-
"""图片时间戳抽取与一致性校验（OCR 可见时间 + EXIF 元数据）。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ExifTags

from app.ai_detection.amount_candidates import DATE_PATTERN, TIME_PATTERN, looks_like_clock_time, normalize_text

DATETIME_COMBINED_PATTERN = re.compile(
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
BAD_SOFTWARE_KEYWORDS = ("photoshop", "picsart", "美图", "snapseed", "lightroom", "醒图", "meitu")

# 触发后直接判「篡改」的时间类异常（可与 config hard_tamper_anomalies 合并）
DEFAULT_HARD_TAMPER_ANOMALIES = frozenset({
    "exif_editing_software",
    "future_datetime",
    "status_transaction_time_mismatch",
    "exif_visible_datetime_mismatch",
    "business_visible_datetime_mismatch",
})


def _parse_exif_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_datetime(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


def _token_clean_text(token: Any) -> str:
    if hasattr(token, "clean_text"):
        return normalize_text(str(token.clean_text))
    if isinstance(token, dict):
        return normalize_text(str(token.get("clean_text") or token.get("text") or ""))
    return normalize_text(str(token))


def _token_bbox(token: Any) -> Tuple[int, int, int, int]:
    if hasattr(token, "bbox"):
        bbox = token.bbox
    elif isinstance(token, dict):
        bbox = token.get("bbox") or (0, 0, 0, 0)
    else:
        bbox = (0, 0, 0, 0)
    return tuple(int(v) for v in bbox[:4])  # type: ignore[return-value]


def _parse_combined_datetime(text: str) -> Optional[datetime]:
    clean = normalize_text(text)
    match = DATETIME_COMBINED_PATTERN.search(clean)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or 0),
        )
    except ValueError:
        return None


def _parse_clock_time(text: str) -> Optional[Tuple[int, int, int]]:
    clean = normalize_text(text)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", clean)
    if not match:
        return None
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour >= 24 or minute >= 60 or second >= 60:
        return None
    return hour, minute, second


def parse_business_datetime(text: Optional[str]) -> Optional[datetime]:
    """解析前端/业务传入的单据时间（多种常见格式）。"""
    raw = str(text or "").strip()
    if not raw:
        return None

    normalized = normalize_text(raw).replace("T", " ")
    parsed = _parse_combined_datetime(normalized)
    if parsed is not None:
        return parsed

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _compare_business_and_visible(
    business_dt: datetime,
    transaction_datetime: Optional[str],
    transaction_time: Optional[str],
    tolerance_seconds: float,
) -> List[str]:
    anomalies: List[str] = []
    visible_dt: Optional[datetime] = None

    if transaction_datetime:
        try:
            visible_dt = datetime.fromisoformat(str(transaction_datetime))
        except ValueError:
            visible_dt = None

    if visible_dt is None and transaction_time:
        visible_dt = _parse_combined_datetime(transaction_time)

    if visible_dt is None:
        return anomalies

    delta = abs((business_dt - visible_dt).total_seconds())
    if delta > tolerance_seconds:
        anomalies.append("business_visible_datetime_mismatch")
    return anomalies


def _resolve_hard_tamper_anomalies(thresholds: Dict[str, Any]) -> frozenset[str]:
    configured = thresholds.get("hard_tamper_anomalies")
    if isinstance(configured, (list, tuple, set)):
        return frozenset(str(item) for item in configured)
    return DEFAULT_HARD_TAMPER_ANOMALIES


def parse_exif_timestamps(image_path: str) -> Dict[str, Any]:
    """解析 EXIF 中的时间与修图软件信息。"""
    result: Dict[str, Any] = {
        "has_exif": False,
        "datetime_original": None,
        "datetime_digitized": None,
        "software": None,
        "suspicious_software": False,
    }
    try:
        with Image.open(image_path) as img_pil:
            exif = img_pil._getexif()
            if not exif:
                return result

            result["has_exif"] = True
            exif_dict = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}

            original_dt = _parse_exif_datetime(
                exif_dict.get("EXIF DateTimeOriginal") or exif_dict.get("DateTimeOriginal")
            )
            digitized_dt = _parse_exif_datetime(
                exif_dict.get("EXIF DateTimeDigitized") or exif_dict.get("DateTimeDigitized")
            )
            fallback_dt = _parse_exif_datetime(exif_dict.get("DateTime"))

            if original_dt:
                result["datetime_original"] = _format_datetime(original_dt)
            elif fallback_dt:
                result["datetime_original"] = _format_datetime(fallback_dt)

            if digitized_dt:
                result["datetime_digitized"] = _format_datetime(digitized_dt)

            software = str(exif_dict.get("Software", "") or "").strip()
            if software:
                result["software"] = software
                lowered = software.lower()
                result["suspicious_software"] = any(key in lowered for key in BAD_SOFTWARE_KEYWORDS)
    except Exception:
        pass
    return result


def extract_timestamps_from_tokens(
    tokens: Sequence[Any],
    image_shape: Tuple[int, int, int],
) -> Dict[str, Any]:
    """从 OCR token 中抽取状态栏时间与正文交易时间。"""
    image_h = int(image_shape[0])
    status_bar_times: List[str] = []
    transaction_entries: List[Tuple[str, Optional[datetime]]] = []

    for token in tokens:
        clean = _token_clean_text(token)
        if not clean:
            continue
        display_text = str(getattr(token, "text", None) or (token.get("text") if isinstance(token, dict) else clean))
        _, y1, _, _ = _token_bbox(token)

        combined_dt = _parse_combined_datetime(clean)
        if combined_dt is not None:
            transaction_entries.append((display_text, combined_dt))
            continue

        if DATE_PATTERN.search(clean) and TIME_PATTERN.search(clean):
            parsed = _parse_combined_datetime(clean.replace(".", "-"))
            transaction_entries.append((display_text, parsed))
            continue

        if looks_like_clock_time(clean):
            if y1 <= image_h * 0.12:
                status_bar_times.append(display_text)
            else:
                transaction_entries.append((display_text, None))
            continue

        if DATE_PATTERN.search(clean) and y1 > image_h * 0.12:
            transaction_entries.append((display_text, None))

    best_transaction: Optional[str] = None
    best_transaction_dt: Optional[datetime] = None
    for text, dt in transaction_entries:
        if dt is not None and (best_transaction_dt is None or len(text) > len(best_transaction or "")):
            best_transaction = text
            best_transaction_dt = dt
    if best_transaction is None and transaction_entries:
        best_transaction = transaction_entries[0][0]

    return {
        "status_bar_time": status_bar_times[0] if status_bar_times else None,
        "transaction_time": best_transaction,
        "transaction_datetime": _format_datetime(best_transaction_dt) if best_transaction_dt else None,
    }


def _clock_minutes(clock: Tuple[int, int, int]) -> int:
    return clock[0] * 60 + clock[1]


def _compare_status_and_transaction(
    status_bar_time: Optional[str],
    transaction_time: Optional[str],
    transaction_datetime: Optional[str],
) -> List[str]:
    anomalies: List[str] = []
    status_clock = _parse_clock_time(status_bar_time) if status_bar_time else None
    if not status_clock:
        return anomalies

    tx_dt = _parse_combined_datetime(transaction_time or "") if transaction_time else None
    if tx_dt is None and transaction_datetime:
        try:
            tx_dt = datetime.fromisoformat(transaction_datetime)
        except ValueError:
            tx_dt = None

    if tx_dt is not None:
        if tx_dt.date() != datetime.now().date() and status_bar_time:
            # 状态栏通常显示“当前截图时刻”，与交易日期不同日本身不一定异常
            pass
        status_minutes = _clock_minutes(status_clock)
        tx_minutes = tx_dt.hour * 60 + tx_dt.minute
        if abs(status_minutes - tx_minutes) > 8:
            anomalies.append("status_transaction_time_mismatch")
        return anomalies

    tx_clock = _parse_clock_time(transaction_time) if transaction_time else None
    if tx_clock and abs(_clock_minutes(status_clock) - _clock_minutes(tx_clock)) > 8:
        anomalies.append("status_transaction_time_mismatch")
    return anomalies


def _compare_exif_and_visible(
    exif_info: Dict[str, Any],
    transaction_datetime: Optional[str],
) -> List[str]:
    anomalies: List[str] = []
    exif_text = exif_info.get("datetime_original") or exif_info.get("datetime_digitized")
    if not exif_text or not transaction_datetime:
        return anomalies

    try:
        exif_dt = datetime.fromisoformat(str(exif_text))
        visible_dt = datetime.fromisoformat(str(transaction_datetime))
    except ValueError:
        return anomalies

    delta_seconds = abs((exif_dt - visible_dt).total_seconds())
    if delta_seconds > 86400:
        anomalies.append("exif_visible_datetime_mismatch")
    return anomalies


def check_image_timestamps(
    image_path: str,
    *,
    ocr_tokens: Optional[Sequence[Any]] = None,
    image_shape: Optional[Tuple[int, int, int]] = None,
    business_datetime: Optional[str] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    综合 EXIF、OCR 可见时间与业务单据时间，输出结构化摘要、异常列表与风险分（0~1）。
    """
    thresh = thresholds or {}
    hard_tamper_set = _resolve_hard_tamper_anomalies(thresh)
    exif_info = parse_exif_timestamps(image_path)
    business_dt = parse_business_datetime(business_datetime)

    ocr_info: Dict[str, Any] = {
        "status_bar_time": None,
        "transaction_time": None,
        "transaction_datetime": None,
    }
    if ocr_tokens and image_shape:
        ocr_info = extract_timestamps_from_tokens(ocr_tokens, image_shape)

    timestamp_check = {
        **ocr_info,
        "business_document_time": business_datetime,
        "business_document_datetime": _format_datetime(business_dt) if business_dt else None,
        "exif_datetime_original": exif_info.get("datetime_original"),
        "exif_datetime_digitized": exif_info.get("datetime_digitized"),
        "has_exif": exif_info.get("has_exif", False),
        "exif_software": exif_info.get("software"),
    }

    anomalies: List[str] = []
    reasons: List[str] = []
    risk = 0.0

    if exif_info.get("suspicious_software"):
        anomalies.append("exif_editing_software")
        reasons.append(f"EXIF检测到修图软件: {exif_info.get('software')}")
        risk = max(risk, float(thresh.get("timestamp_software_risk", 0.85)))

    anomalies.extend(
        _compare_status_and_transaction(
            ocr_info.get("status_bar_time"),
            ocr_info.get("transaction_time"),
            ocr_info.get("transaction_datetime"),
        )
    )
    anomalies.extend(
        _compare_exif_and_visible(exif_info, ocr_info.get("transaction_datetime"))
    )

    if business_dt is not None:
        tolerance = float(thresh.get("business_time_tolerance_seconds", 300))
        anomalies.extend(
            _compare_business_and_visible(
                business_dt,
                ocr_info.get("transaction_datetime"),
                ocr_info.get("transaction_time"),
                tolerance,
            )
        )

    for dt_text in (
        ocr_info.get("transaction_datetime"),
        exif_info.get("datetime_original"),
        exif_info.get("datetime_digitized"),
    ):
        if not dt_text:
            continue
        try:
            parsed = datetime.fromisoformat(str(dt_text))
            if parsed > datetime.now():
                anomalies.append("future_datetime")
                break
        except ValueError:
            continue

    anomaly_messages = {
        "status_transaction_time_mismatch": "状态栏时间与交易时间不一致",
        "exif_visible_datetime_mismatch": "EXIF时间与可见交易时间相差超过1天",
        "business_visible_datetime_mismatch": "业务单据时间与图片可见交易时间不一致",
        "future_datetime": "检测到未来时间",
        "exif_editing_software": "EXIF含修图软件标记",
    }
    for code in anomalies:
        if code in anomaly_messages and anomaly_messages[code] not in reasons:
            reasons.append(anomaly_messages[code])

    mismatch_risk = float(thresh.get("timestamp_mismatch_risk", 0.58))
    future_risk = float(thresh.get("timestamp_future_risk", 0.72))
    business_risk = float(thresh.get("timestamp_business_mismatch_risk", 0.80))
    if "status_transaction_time_mismatch" in anomalies or "exif_visible_datetime_mismatch" in anomalies:
        risk = max(risk, mismatch_risk)
    if "business_visible_datetime_mismatch" in anomalies:
        risk = max(risk, business_risk)
    if "future_datetime" in anomalies:
        risk = max(risk, future_risk)

    timestamp_check["anomalies"] = list(dict.fromkeys(anomalies))
    hard_tamper = bool(hard_tamper_set.intersection(timestamp_check["anomalies"]))
    return {
        "timestamp_check": timestamp_check,
        "risk": float(min(1.0, risk)),
        "reasons": reasons,
        "anomalies": timestamp_check["anomalies"],
        "hard_tamper": hard_tamper,
    }
