use alsa::pcm::{Access, Format, HwParams, PCM};
use alsa::Direction;
use anyhow::{Context, Result};
use std::time::Duration;
use tokio::sync::mpsc;

use crate::config::DeviceConfig;

pub struct AudioChunk {
    pub device_name: String,
    /// "mono" | "all" | "left" | "right" | "ch0" | "ch1" | …
    pub channel_label: String,
    pub channels: u16,
    pub sample_rate: u32,
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub samples: Vec<i16>,
}

/// Spawns a blocking OS thread that captures audio from one ALSA device
/// and pushes `AudioChunk`s to the async delivery layer.
pub fn spawn_capture(
    device: DeviceConfig,
    chunk_duration: Duration,
    tx: mpsc::Sender<AudioChunk>,
) {
    std::thread::spawn(move || {
        let name = device.name.clone();
        if let Err(e) = capture_loop(&device, chunk_duration, &tx) {
            tracing::error!("Capture thread '{}' stopped: {:#}", name, e);
        }
    });
}

fn capture_loop(
    device: &DeviceConfig,
    chunk_duration: Duration,
    tx: &mpsc::Sender<AudioChunk>,
) -> Result<()> {
    let pcm = PCM::new(&device.alsa_device, Direction::Capture, false)
        .with_context(|| format!("Cannot open ALSA device '{}'", device.alsa_device))?;

    let hwp = HwParams::any(&pcm)?;
    hwp.set_channels(device.channels)?;
    hwp.set_rate(device.sample_rate, alsa::ValueOr::Nearest)?;
    hwp.set_format(Format::s16())?;
    hwp.set_access(Access::RWInterleaved)?;
    hwp.set_period_size(1024, alsa::ValueOr::Nearest)?;
    hwp.set_buffer_size(8192)?;
    pcm.hw_params(&hwp)?;
    pcm.start()?;

    let io = pcm.io_i16()?;
    let frames_per_chunk = (device.sample_rate as u64 * chunk_duration.as_secs()) as usize;
    let samples_per_chunk = frames_per_chunk * device.channels as usize;
    let mut read_buf = vec![0i16; 1024 * device.channels as usize];
    let mut acc: Vec<i16> = Vec::with_capacity(samples_per_chunk * 2);

    tracing::info!(
        "Recording '{}' on {} — {} Hz × {} ch, chunk = {} s",
        device.name,
        device.alsa_device,
        device.sample_rate,
        device.channels,
        chunk_duration.as_secs()
    );

    loop {
        let frames = match io.readi(&mut read_buf) {
            Ok(n) => n,
            Err(e) => {
                tracing::warn!("ALSA xrun on '{}': {}. Recovering.", device.name, e);
                pcm.prepare().ok();
                continue;
            }
        };

        acc.extend_from_slice(&read_buf[..frames * device.channels as usize]);

        while acc.len() >= samples_per_chunk {
            let raw: Vec<i16> = acc.drain(..samples_per_chunk).collect();
            let ts = chrono::Utc::now();
            for chunk in build_chunks(device, raw, ts) {
                if tx.blocking_send(chunk).is_err() {
                    return Ok(());
                }
            }
        }
    }
}

/// Produces one chunk per stream target:
/// - stereo with split_channels=true  → "all" + "left" + "right" (3 chunks)
/// - stereo with split_channels=false → "all" (1 chunk)
/// - mono                             → "mono" (1 chunk)
fn build_chunks(
    device: &DeviceConfig,
    raw: Vec<i16>,
    ts: chrono::DateTime<chrono::Utc>,
) -> Vec<AudioChunk> {
    let ch = device.channels as usize;
    let mut out = Vec::new();

    if ch > 1 && device.split_channels {
        // Full interleaved recording
        out.push(AudioChunk {
            device_name: device.name.clone(),
            channel_label: "all".into(),
            channels: ch as u16,
            sample_rate: device.sample_rate,
            timestamp: ts,
            samples: raw.clone(),
        });
        // Per-channel demultiplexed mono recordings
        for c in 0..ch {
            let mono: Vec<i16> = raw.iter().skip(c).step_by(ch).copied().collect();
            out.push(AudioChunk {
                device_name: device.name.clone(),
                channel_label: channel_label(c, ch),
                channels: 1,
                sample_rate: device.sample_rate,
                timestamp: ts,
                samples: mono,
            });
        }
    } else {
        let label = if ch == 1 { "mono" } else { "all" };
        out.push(AudioChunk {
            device_name: device.name.clone(),
            channel_label: label.into(),
            channels: ch as u16,
            sample_rate: device.sample_rate,
            timestamp: ts,
            samples: raw,
        });
    }

    out
}

fn channel_label(idx: usize, total: usize) -> String {
    match (idx, total) {
        (0, 2) => "left".into(),
        (1, 2) => "right".into(),
        _      => format!("ch{}", idx),
    }
}
