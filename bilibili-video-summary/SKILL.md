---
name: bilibili-video-summary
description: Transcribe and summarize Bilibili videos from a bilibili.com or b23.tv link, BV/av identifier, or selected multi-part video. Prefer available human or AI subtitles, fall back to local whisper.cpp speech recognition, and preserve timestamps and source boundaries. Use when the user asks to 转写、总结、分析、提取字幕或整理 B 站视频笔记; do not use for unrelated local media or other platforms.
---

# B站视频转写与总结

Turn a Bilibili source into inspectable local evidence before writing a summary.

## Workflow

1. Identify the exact video and part. Preserve an explicit `?p=N`; if a multi-part link is ambiguous, ask which part only when the answer materially changes the task.
2. Run `scripts/prepare_bilibili.py` from this skill directory. Put outputs under the current task workspace, normally `output/bilibili/`:

   ```bash
   python3 <skill-dir>/scripts/prepare_bilibili.py "<Bilibili URL or BV id>" \
     --output-root "<workspace>/output/bilibili"
   ```

3. The script prefers a matching human/AI subtitle track. If none is usable, it downloads audio and uses the existing local whisper.cpp model. It keeps `metadata.json`, `transcript.txt`, and `transcript.srt`; temporary WAV and downloaded audio are removed after successful ASR unless `--keep-audio` is specified.
4. Read both `metadata.json` and the complete `transcript.txt` before summarizing. Do not infer the video's content from its title, description, or comments.
5. Match the deliverable to the request. A normal summary should include the source card, one-sentence thesis, timestamped structure, key explanations, and uncertain names/numbers. A transcript-only request should return the artifact path without adding an unsolicited essay.

## Evidence boundaries

- Distinguish what the speaker says from Codex's explanation, correction, or external verification.
- Treat Whisper homophones, names, model names, code terms, and numbers as uncertain unless context makes the correction clear. Preserve the original timestamp when noting uncertainty.
- Platform subtitles can also be incomplete or machine-generated. Report `transcript_source` from `metadata.json` rather than presenting all text as verbatim human subtitles.
- Audio transcription does not recover diagrams, code shown only on screen, or hardcoded visual text. Say when visual inspection is still needed.

## Authentication and failure handling

- Default to anonymous extraction. Never read browser cookies silently.
- For content the user is authorized to access, use `--cookies-from-browser <browser>` only after the user explicitly authorizes access to that browser session. Never print, copy, or save raw cookies.
- If extraction fails, report the failing stage and retained artifacts. Do not claim that a video is unavailable merely because one anonymous request failed.
- Use `--force-asr` only when the user requests fresh speech recognition or platform subtitles are demonstrably unsuitable. Use `--subtitle-only` when audio download/ASR is not desired.

## Useful options

```text
--part N                    select a multi-part video
--language zh              Whisper language, or auto
--model /path/model.bin    override whisper.cpp model
--force                     rebuild existing output
--force-asr                 ignore available subtitles
--subtitle-only             fail instead of downloading audio
--keep-audio                retain downloaded compressed audio
--cookies-from-browser NAME use an authorized browser session
```
