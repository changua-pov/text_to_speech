mod audio;
mod aws_client;
mod config;
mod wav_encoder;

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use clap::Parser;
use tokio::sync::mpsc;

#[derive(Parser, Debug)]
#[command(name = "raspi-audio-recorder")]
#[command(about = "Stream Raspberry Pi microphone audio to AWS (Firehose / Kinesis / Lambda)")]
struct Args {
    /// Path to the JSON config file.
    #[arg(short, long, default_value = "config.json")]
    config: String,

    /// List detected ALSA cards and exit.
    #[arg(short = 'l', long)]
    list_devices: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();

    if args.list_devices {
        list_alsa_devices();
        return Ok(());
    }

    let raw = std::fs::read_to_string(&args.config)
        .map_err(|e| anyhow::anyhow!("Cannot read '{}': {}", args.config, e))?;
    let cfg: config::Config = serde_json::from_str(&raw)?;

    tracing::info!("Delivery mode : {:?}", cfg.aws.delivery_mode);
    tracing::info!("Devices       : {}", cfg.devices.len());

    let aws = Arc::new(aws_client::AwsDelivery::new(cfg.aws).await?);
    let chunk_duration = Duration::from_secs(cfg.recording.chunk_duration_secs);

    let (tx, mut rx) = mpsc::channel::<audio::AudioChunk>(512);

    for device in cfg.devices {
        audio::spawn_capture(device, chunk_duration, tx.clone());
    }
    drop(tx); // Channel closes when all capture threads exit.

    while let Some(chunk) = rx.recv().await {
        let aws = Arc::clone(&aws);
        tokio::spawn(async move {
            if let Err(e) = aws.deliver(&chunk).await {
                tracing::error!(
                    "Delivery failed [{}/{}]: {:#}",
                    chunk.device_name, chunk.channel_label, e
                );
            }
        });
    }

    tracing::info!("All capture threads exited. Shutting down.");
    Ok(())
}

fn list_alsa_devices() {
    println!("Detected ALSA cards:");
    for card in alsa::card::Iter::new().flatten() {
        let name = card.get_name().unwrap_or_else(|_| "(unknown)".into());
        println!(
            "  Card #{:>2}: {}  →  alsa_device: \"hw:{},0\"",
            card.get_index(),
            name,
            card.get_index()
        );
    }
    println!();
    println!("Tip: run  arecord -l  for full device/subdevice details.");
}
