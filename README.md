# Bilibili Video Summary Skill

一个面向 Codex 的 B 站视频转写与总结 Skill。

给出 `bilibili.com`、`b23.tv`、BV/av 号或分 P 链接后，它会先尝试提取平台人工/AI 字幕；没有可用字幕时，再下载公开音频并使用本地 `whisper.cpp` 转写。最终保留带时间戳的文本、SRT 字幕和来源元数据，便于 Codex 做可追溯的总结与学习笔记。

## 主要能力

- 支持 B 站标准链接、`b23.tv` 短链接、BV/av 号和多分 P 视频
- 字幕优先，减少不必要的音频下载和 ASR 误差
- 无字幕时复用本地 `whisper.cpp`，无需云端转写 API Key
- 输出 `transcript.txt`、`transcript.srt` 和 `metadata.json`
- 区分平台字幕、Whisper 转写、视频原话和 Codex 补充分析
- 默认匿名访问；不会静默读取浏览器 Cookie
- 公开接口失败时可使用 `yt-dlp` 作为备用路线

## 安装

在 Codex 中使用 `$skill-installer`：

```text
Use $skill-installer to install the skill from:
https://github.com/PPPPcw/bilibili-video-summary-skill/tree/main/bilibili-video-summary
```

也可以手动安装：

```bash
git clone https://github.com/PPPPcw/bilibili-video-summary-skill.git
mkdir -p ~/.codex/skills
cp -R bilibili-video-summary-skill/bilibili-video-summary ~/.codex/skills/bilibili-video-summary
```

## 本地依赖

macOS：

```bash
brew install ffmpeg whisper-cpp yt-dlp
```

`yt-dlp` 是备用路线；平台字幕可用时不会运行 Whisper。若需要 ASR，请准备 whisper.cpp GGML 模型：

```bash
mkdir -p ~/.codex/skills/bilibili-video-summary/models
curl -L \
  -o ~/.codex/skills/bilibili-video-summary/models/ggml-small.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

也可以通过 `--model /path/to/model.bin` 或环境变量 `WHISPER_CPP_MODEL` 指定模型。

## 使用

自然语言调用：

```text
帮我转写并总结这个 B 站视频：https://www.bilibili.com/video/BV...
```

显式调用：

```text
Use $bilibili-video-summary to transcribe this Bilibili video with timestamps: BV...
```

脚本也可以独立运行：

```bash
python3 ~/.codex/skills/bilibili-video-summary/scripts/prepare_bilibili.py \
  "https://www.bilibili.com/video/BV..." \
  --output-root ./output/bilibili
```

常用参数：

```text
--part N                    选择多分 P 视频中的第 N P
--language zh              Whisper 语言，也可以设为 auto
--model /path/model.bin    指定 whisper.cpp 模型
--force                     重新生成已有产物
--force-asr                 忽略平台字幕，强制语音识别
--subtitle-only             没有字幕时停止，不下载音频
--keep-audio                保留下载的压缩音频
--cookies-from-browser NAME 使用用户明确授权的浏览器会话作为备用路线
```

## 输出结构

```text
output/bilibili/<video-id>[-pN]/
├── metadata.json
├── transcript.srt
└── transcript.txt
```

`metadata.json` 会记录标题、UP 主、视频/分 P 标识、时长以及 `transcript_source`。Whisper 成功后，临时 WAV 和压缩音频默认会被清理。

## 工作路线

```text
Bilibili URL / BV / av
        ↓
公开元数据与分 P 解析
        ↓
人工/AI 字幕可用？ ── 是 → 生成带时间戳文本
        │
        否
        ↓
获取公开音频 → FFmpeg 转 WAV → whisper.cpp → TXT + SRT
```

## 隐私与边界

- 默认仅访问公开 B 站接口和公开媒体流。
- 只有用户明确授权后，才应使用 `--cookies-from-browser`。
- 脚本不会输出、复制或保存原始 Cookie。
- 音频转写无法恢复只出现在画面里的代码、图表或硬字幕。
- 请遵守 Bilibili 服务条款、版权要求以及所在地法律，仅处理你有权访问和使用的内容。

## 测试

离线单元测试：

```bash
python3 -m unittest discover -s tests -v
```

该测试不访问 Bilibili，也不会下载模型或媒体。

## License

[MIT](LICENSE)
