"""提示文案多语言（NOTIFY_LANG 配置，默认 zh）。
覆盖 speakr 界面语言：zh/en/de/es/fr/pt/ru。标签 SW-Lx 语言中立，不在此处理。
"""
NL = chr(10)

LOCALES = {
    "zh": {
        "alert_title": "⚠ 敏感词提示 {ts} — 命中 {n} 处（最高 {level}）",
        "summary_heading": "## ⚠️ 敏感词涉及语句清单（生成于 {ts}，共 {n} 处）",
        "hit_label": "命中：{term}",
        "llm_verdict": "（LLM 判定）",
        "overflow": "…共 {n} 处，其余见审计日志",
    },
    "en": {
        "alert_title": "⚠ Sensitive-word alert {ts} — {n} hits (max {level})",
        "summary_heading": "## ⚠️ Sensitive-word sentence list (generated {ts}, {n} hits)",
        "hit_label": "hit: {term}",
        "llm_verdict": "(LLM verdict)",
        "overflow": "... {n} hits in total, see audit log for the rest",
    },
    "de": {
        "alert_title": "⚠ Hinweis zu sensiblen Begriffen {ts} — {n} Treffer (max. {level})",
        "summary_heading": "## ⚠️ Liste der Sätze mit sensiblen Begriffen (erstellt {ts}, {n} Treffer)",
        "hit_label": "Treffer: {term}",
        "llm_verdict": "(LLM-Bewertung)",
        "overflow": "... insgesamt {n} Treffer, weitere im Audit-Log",
    },
    "es": {
        "alert_title": "⚠ Alerta de palabras sensibles {ts} — {n} coincidencias (máx. {level})",
        "summary_heading": "## ⚠️ Lista de frases con palabras sensibles (generada {ts}, {n} coincidencias)",
        "hit_label": "coincidencia: {term}",
        "llm_verdict": "(veredicto LLM)",
        "overflow": "... {n} coincidencias en total, el resto en el registro de auditoría",
    },
    "fr": {
        "alert_title": "⚠ Alerte mots sensibles {ts} — {n} occurrences (max {level})",
        "summary_heading": "## ⚠️ Liste des phrases contenant des mots sensibles (générée le {ts}, {n} occurrences)",
        "hit_label": "occurrence : {term}",
        "llm_verdict": "(verdict LLM)",
        "overflow": "... {n} occurrences au total, le reste dans le journal d'audit",
    },
    "pt": {
        "alert_title": "⚠ Alerta de palavras sensíveis {ts} — {n} ocorrências (máx. {level})",
        "summary_heading": "## ⚠️ Lista de frases com palavras sensíveis (gerada em {ts}, {n} ocorrências)",
        "hit_label": "ocorrência: {term}",
        "llm_verdict": "(veredito LLM)",
        "overflow": "... {n} ocorrências no total, demais no registro de auditoria",
    },
    "ru": {
        "alert_title": "⚠ Предупреждение о чувствительных словах {ts} — совпадений: {n} (макс. {level})",
        "summary_heading": "## ⚠️ Список фраз с чувствительными словами (создан {ts}, всего {n})",
        "hit_label": "совпадение: {term}",
        "llm_verdict": "(оценка LLM)",
        "overflow": "... всего {n} совпадений, остальное в журнале аудита",
    },
    "id": {
        "alert_title": "⚠ Peringatan kata sensitif {ts} — {n} temuan (maks. {level})",
        "summary_heading": "## ⚠️ Daftar kalimat yang mengandung kata sensitif (dibuat {ts}, {n} temuan)",
        "hit_label": "temuan: {term}",
        "llm_verdict": "(penilaian LLM)",
        "overflow": "... total {n} temuan, lainnya di log audit",
    },
}


def t(key: str, lang: str) -> str:
    return LOCALES.get(lang, LOCALES["zh"]).get(key) or LOCALES["zh"][key]
