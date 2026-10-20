from __future__ import annotations

import hashlib
import re


TAG_KIND_ALIASES = {
    "primary": "primary",
    "secondary": "secondary",
    "method": "method",
    "task": "task",
    "structure": "structure",
    "constraint": "constraint",
}


def normalize_label(label: str) -> str:
    value = str(label).strip()
    value = value.replace("，", "、").replace(",", "、")
    value = re.sub(r"\s+", "", value)
    value = value.replace("概率统计", "概率")
    return value


def normalize_question_text(text: str) -> str:
    value = str(text).lower()
    value = value.replace("，", ",").replace("。", ".").replace("：", ":").replace("；", ";")
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[`*_#>\-]+", "", value)
    return value


def sha256_normalized(text: str) -> str:
    return hashlib.sha256(normalize_question_text(text).encode("utf-8")).hexdigest()


def family_id_from_text(text: str) -> str:
    return f"fam:{sha256_normalized(text)[:16]}"
