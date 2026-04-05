"""
guardrails/post_tool_use.py
===========================
Layer 3 — PostToolUse Event Handler.

Dipanggil setelah setiap tool selesai dieksekusi.
Memvalidasi output dan mencatat anomali.
Sesuai CLAUDE.md Seksi 3.3.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from guardrails.base_hook import BaseHook
from shared.schemas import ValidationResultSchema
from shared.logger import get_logger

logger = get_logger("post_tool_use")


class PostToolUseHook(BaseHook):
    """
    Event hook yang dijalankan SETELAH setiap tool call selesai.

    Fungsi:
    1. Validasi struktur output dari tool.
    2. Log anomali untuk observability.
    3. Sanitasi output sebelum dikembalikan ke agen.
    """

    hook_name = "post_tool_use"

    def pre_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ValidationResultSchema:
        """PostToolUseHook tidak intervensi di fase pre. Return valid."""
        return ValidationResultSchema(is_valid=True)

    def post_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Validasi dan log output dari tool.

        Args:
            tool_name: Tool yang baru saja dieksekusi.
            parameters: Parameter yang digunakan.
            result: Output mentah dari tool.
            context: Session context opsional.

        Returns:
            Result yang telah divalidasi (atau None jika anomali kritis).
        """
        errors = []
        warnings = []

        # 1. Cek apakah result None (kemungkinan tool gagal diam-diam)
        if result is None:
            warnings.append(f"Tool '{tool_name}' mengembalikan None. Kemungkinan terjadi kegagalan.")
            logger.warning(
                f"[POST_TOOL_USE] Tool returned None",
                agent=self.agent_name,
                tool=tool_name,
            )
            return result

        # 2. Jika result adalah dict, cek apakah ada field error
        if isinstance(result, dict):
            if "error" in result and result["error"]:
                errors.append(f"Tool '{tool_name}' mengembalikan error: {result['error']}")
                logger.error(
                    f"[POST_TOOL_USE] Tool returned error field",
                    agent=self.agent_name,
                    tool=tool_name,
                    error=result.get("error"),
                )

            # 3. Deteksi data sensitif yang tidak seharusnya ada di output
            sensitive_keys = {"password", "secret", "api_key", "token", "credential"}
            found_sensitive = sensitive_keys.intersection(set(str(k).lower() for k in result.keys()))
            if found_sensitive:
                warnings.append(f"Output mengandung field sensitif: {found_sensitive}. Disanitasi.")
                for key in list(result.keys()):
                    if str(key).lower() in sensitive_keys:
                        result[key] = "[REDACTED]"
                logger.warning(
                    f"[POST_TOOL_USE] Sensitive fields redacted from output",
                    agent=self.agent_name,
                    tool=tool_name,
                    redacted_keys=list(found_sensitive),
                )

        # 4. Cek serialisability (output harus bisa di-JSON-kan)
        try:
            json.dumps(result, default=str)
        except (TypeError, ValueError) as e:
            warnings.append(f"Output '{tool_name}' tidak bisa di-serialisasi: {e}")
            logger.warning(
                f"[POST_TOOL_USE] Output not JSON-serializable",
                agent=self.agent_name,
                tool=tool_name,
            )

        # Log sukses jika tidak ada error
        if not errors:
            self._log_post(tool_name, success=True)
        else:
            self._log_post(tool_name, success=False)

        return result
