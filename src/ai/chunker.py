"""Semantic chunking for RAG — split resume and JD by structure boundaries."""

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    text: str
    source: str          # "resume" | "job" | "supplement"
    job_id: str = ""
    section: str = ""
    metadata: dict = field(default_factory=dict)


# Rough token estimate: 1 Chinese char ≈ 1 token, 1 English word ≈ 1 token
def _estimate_tokens(text: str) -> int:
    return len(text)


def _split_by_paragraphs(text: str, max_tokens: int = 400) -> list[str]:
    """Split text by blank lines, then by sentences if a segment is too long."""
    segments = re.split(r'\n\s*\n', text.strip())
    chunks = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if _estimate_tokens(seg) <= max_tokens:
            chunks.append(seg)
        else:
            # Split by sentence boundaries
            sub = _split_by_sentences(seg, max_tokens)
            chunks.extend(sub)
    return chunks


def _split_by_sentences(text: str, max_tokens: int = 400) -> list[str]:
    """Split long text by sentence boundaries (Chinese/English)."""
    parts = re.split(r'(?<=[。！？；\n])', text)
    chunks = []
    current = ""
    for part in parts:
        if not part.strip():
            continue
        if _estimate_tokens(current + part) <= max_tokens:
            current += part
        else:
            if current:
                chunks.append(current.strip())
            current = part
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text.strip()]


def _detect_section(text: str) -> str:
    """Heuristic: detect section type from heading or content."""
    lower = text[:80].lower()
    if any(k in lower for k in ("技能", "skill", "技术栈", "技术能力")):
        return "skills"
    if any(k in lower for k in ("经验", "experience", "工作经历", "工作经历", "项目")):
        return "experience"
    if any(k in lower for k in ("教育", "education", "学历", "学校")):
        return "education"
    if any(k in lower for k in ("自我", "summary", "简介", "概述")):
        return "summary"
    return "other"


class Chunker:
    """Split resume and JD text into semantic chunks."""

    def split_resume(self, text: str, source: str = "resume") -> list[Chunk]:
        if not text or not text.strip():
            return []

        # Try markdown heading split first
        heading_chunks = re.split(r'\n(?=#{1,3}\s)', text.strip())
        if len(heading_chunks) > 1:
            segments = heading_chunks
        else:
            segments = None

        if not segments:
            segments = _split_by_paragraphs(text, max_tokens=400)

        chunks = []
        for i, seg in enumerate(segments):
            seg = seg.strip()
            if not seg:
                continue
            # If segment still too long, split by sentences
            if _estimate_tokens(seg) > 400:
                sub_parts = _split_by_sentences(seg, max_tokens=400)
                for j, sub in enumerate(sub_parts):
                    chunk_id = f"{source}_{i}_{j}" if len(sub_parts) > 1 else f"{source}_{i}"
                    section = _detect_section(sub)
                    chunks.append(Chunk(id=chunk_id, text=sub, source=source, section=section))
            else:
                chunk_id = f"{source}_{i}"
                section = _detect_section(seg)
                chunks.append(Chunk(id=chunk_id, text=seg, source=source, section=section))

        return chunks

    def split_jd(self, job_id: str, title: str, jd_text: str,
                 skill_tags: list = None) -> list[Chunk]:
        chunks = []

        # Title as a chunk
        if title:
            chunks.append(Chunk(
                id=f"job_{job_id}_title",
                text=f"职位: {title}",
                source="job",
                job_id=job_id,
                section="title",
            ))

        # Skill tags as independent chunk
        if skill_tags:
            if isinstance(skill_tags, str):
                skill_tags = [s.strip() for s in skill_tags.split(",") if s.strip()]
            if skill_tags:
                chunks.append(Chunk(
                    id=f"job_{job_id}_skills",
                    text=f"技能要求: {', '.join(skill_tags)}",
                    source="job",
                    job_id=job_id,
                    section="skills",
                    metadata={"skill_tags": skill_tags},
                ))

        # JD body by paragraphs
        if jd_text and jd_text.strip():
            paragraphs = _split_by_paragraphs(jd_text, max_tokens=400)
            for i, para in enumerate(paragraphs):
                para = para.strip()
                if not para:
                    continue
                if _estimate_tokens(para) > 400:
                    sub_parts = _split_by_sentences(para, max_tokens=400)
                    for j, sub in enumerate(sub_parts):
                        chunks.append(Chunk(
                            id=f"job_{job_id}_jd_{i}_{j}",
                            text=sub,
                            source="job",
                            job_id=job_id,
                            section="jd",
                        ))
                else:
                    chunks.append(Chunk(
                        id=f"job_{job_id}_jd_{i}",
                        text=para,
                        source="job",
                        job_id=job_id,
                        section="jd",
                    ))

        return chunks
