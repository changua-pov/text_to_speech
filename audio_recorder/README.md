# raspi-audio-recorder

Rust service that runs on a **Raspberry Pi (Ubuntu/Raspberry Pi OS)** and streams audio from one or more USB/analog microphones to AWS in real time.

Each microphone is recorded independently. For stereo (or multi-channel) microphones every individual channel is also saved as a separate mono track. Audio is encoded as **WAV (PCM 16-bit LE)** and delivered in configurable time chunks.

## Delivery backends

| Mode | How it works |
|------|--------------|
| `firehose` | One Kinesis Data Firehose delivery stream per device+channel → S3 |
| `kinesis` | Single Kinesis Data Stream; partition key = `{device}-{channel}` |
| `lambda` | One Lambda invocation per chunk; payload is JSON with `audio_base64` (WAV) |

## What gets recorded

For a stereo microphone named `mic0` with `split_channels: true`:

```
mic0 / all    — full stereo WAV  (interleaved L+R)
mic0 / left   — left  channel only (mono WAV)
mic0 / right  — right channel only (mono WAV)
```

For a mono microphone named `mic1`:

```
mic1 / mono   — mono WAV
```

## Project structure

```
audio_recorder/
├── Cargo.toml
├── config.example.json          # copy to config.json and edit
├── raspi-audio-recorder.service # systemd unit
└── src/
    ├── main.rs          # CLI entry point, async orchestration
    ├── config.rs        # Config structs (JSON)
    ├── audio.rs         # ALSA capture + channel demux
    ├── aws_client.rs    # Firehose / Kinesis / Lambda delivery
    └── wav_encoder.rs   # In-memory WAV encoder (no external deps)
```

## Prerequisites

### On the Raspberry Pi / Ubuntu

```bash
# ALSA development headers
sudo apt install libasound2-dev pkg-config build-essential

# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

### AWS credentials

Configure via an **IAM instance profile** (recommended) or environment variables:

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1
```

Minimum IAM permissions required:

| Backend | Permission |
|---------|------------|
| Firehose | `firehose:PutRecord` |
| Kinesis  | `kinesis:PutRecord` |
| Lambda   | `lambda:InvokeFunction` |

## Build

```bash
cd audio_recorder

# Native build on the Raspberry Pi
cargo build --release

# Cross-compile for Raspberry Pi 4/5 (aarch64) from an x86_64 host
cargo install cross --git https://github.com/cross-rs/cross
cross build --target aarch64-unknown-linux-gnu --release

# Cross-compile for Raspberry Pi 2/3 (armv7)
cross build --target armv7-unknown-linux-gnueabihf --release
```

## Configuration

```bash
cp config.example.json config.json
# edit config.json
```

### Key fields

| Field | Description |
|-------|-------------|
| `aws.delivery_mode` | `"firehose"`, `"kinesis"`, or `"lambda"` |
| `devices[].alsa_device` | ALSA device string — run `./raspi-audio-recorder -l` to list |
| `devices[].channels` | `1` = mono, `2` = stereo |
| `devices[].split_channels` | `true` → stereo mic also saves left/right individually |
| `recording.chunk_duration_secs` | Seconds of audio per AWS record (recommended: 5–30) |

### Firehose stream naming

With `stream_name_prefix = "raspi-audio"` and the example config, you need these Firehose delivery streams pre-created in AWS:

```
raspi-audio-mic0-all
raspi-audio-mic0-left
raspi-audio-mic0-right
raspi-audio-mic1-mono
```

Each stream should deliver to an S3 bucket. A recommended S3 prefix is:
`audio/{device}/{channel}/!{timestamp:yyyy}/!{timestamp:MM}/!{timestamp:dd}/`

## Usage

```bash
# Detect ALSA devices
./target/release/raspi-audio-recorder --list-devices

# Start recording
./target/release/raspi-audio-recorder --config config.json

# Verbose logging
RUST_LOG=debug ./target/release/raspi-audio-recorder --config config.json
```

## Systemd service

```bash
# Copy binary and config to /home/pi/raspi-audio-recorder/
sudo cp raspi-audio-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raspi-audio-recorder

# Check logs
journalctl -u raspi-audio-recorder -f
```

## Lambda payload format

When using `delivery_mode: "lambda"`, each invocation receives:

```json
{
  "device":       "mic0",
  "channel":      "left",
  "sample_rate":  44100,
  "channels":     1,
  "timestamp":    "2025-01-15T10:30:00Z",
  "key":          "mic0-left-20250115T103000Z",
  "audio_base64": "UklGRiQA...",
  "format":       "wav"
}
```

The Lambda function is responsible for decoding `audio_base64` and persisting the WAV file (e.g. to S3 via `boto3`).

## Size limits

| Backend | Limit | 44100 Hz stereo 16-bit — max safe chunk |
|---------|-------|------------------------------------------|
| Firehose | 1 MB / record | ~5 s (all channels) |
| Kinesis | 1 MB / record | ~5 s (all channels) |
| Lambda sync | 6 MB payload | ~28 s (incl. base64 overhead) |

For high sample rates or multi-channel mics, reduce `chunk_duration_secs`.
