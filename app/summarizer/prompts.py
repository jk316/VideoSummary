"""Map-Reduce 总结的默认 Prompt 模板（中文/英文/双语）。

占位符说明：
  {title} {author} {duration} — 视频元信息
  {chunk_text} — 当前 chunk 转写文本（Map 阶段）
  {start} {end} — chunk 时间范围（HH:MM:SS）
  {chunk_summaries} — 各 chunk 摘要拼接（Reduce 阶段）
"""

ZH_MAP = """你是一位专业的视频内容分析助手。请用中文总结以下视频片段的关键内容。

视频标题：{title}
作者：{author}
片段时间：{start} — {end}

要求：
- 提取核心观点和关键事实
- 保留重要的数字、人名、术语
- 用简洁的要点形式呈现（3-5 条）
- 不要添加原文中没有的信息

片段文本：
{chunk_text}"""

ZH_REDUCE = """你是一位专业的视频内容分析助手。请根据以下各片段的摘要，生成整个视频的完整总结。

视频标题：{title}
作者：{author}
总时长：{duration}

各片段摘要：
{chunk_summaries}

要求：
1. 先写一段 2-4 句的整体摘要
2. 按章节列出内容要点，每条标注时间戳
3. 最后列出 3-5 条关键结论
4. 使用 Markdown 格式，时间戳使用原文中提供的链接
5. 用中文输出"""

EN_MAP = """You are a professional video content analyst.
Summarize the key content of the following video segment in English.

Video Title: {title}
Author: {author}
Segment Time: {start} — {end}

Requirements:
- Extract core ideas and key facts
- Preserve important numbers, names, and terms
- Present as concise bullet points (3-5 items)
- Do not add information not in the original text

Segment Text:
{chunk_text}"""

EN_REDUCE = """You are a professional video content analyst.
Based on the summaries of each segment, generate a complete summary.

Video Title: {title}
Author: {author}
Duration: {duration}

Segment Summaries:
{chunk_summaries}

Requirements:
1. Start with an overall summary (2-4 sentences)
2. List key points by chapter, each with timestamp
3. End with 3-5 key takeaways
4. Use Markdown format
5. Output in English"""

BILINGUAL_REDUCE = """你是一位专业的视频内容分析助手。
请根据以下各片段的摘要，生成整个视频的完整双语总结。

视频标题 / Video Title：{title}
作者 / Author：{author}
总时长 / Duration：{duration}

各片段摘要 / Segment Summaries：
{chunk_summaries}

要求 / Requirements：
1. 整体摘要（中文 + English）
2. 按章节列出内容要点 / Chapter-by-chapter key points
3. 关键结论 / Key takeaways（3-5 条）
4. 使用 Markdown 格式，时间戳使用原文中提供的链接"""


def get_default_prompts(language: str) -> dict[str, str]:
    """根据语言返回默认的 Map / Reduce Prompt。

    Returns:
        {"map": map_template, "reduce": reduce_template}
    """
    if language == "en":
        return {"map": EN_MAP, "reduce": EN_REDUCE}
    if language == "bilingual":
        return {"map": ZH_MAP, "reduce": BILINGUAL_REDUCE}  # Map 用中文，Reduce 双译
    return {"map": ZH_MAP, "reduce": ZH_REDUCE}
