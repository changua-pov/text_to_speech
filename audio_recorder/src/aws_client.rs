use anyhow::{anyhow, Result};
use aws_config::BehaviorVersion;
use base64::prelude::*;
use serde_json::json;

use crate::audio::AudioChunk;
use crate::config::{AwsConfig, DeliveryMode};
use crate::wav_encoder::encode_wav;

pub struct AwsDelivery {
    cfg:      AwsConfig,
    firehose: Option<aws_sdk_firehose::Client>,
    kinesis:  Option<aws_sdk_kinesis::Client>,
    lambda:   Option<aws_sdk_lambda::Client>,
}

impl AwsDelivery {
    pub async fn new(cfg: AwsConfig) -> Result<Self> {
        let sdk_cfg = aws_config::defaults(BehaviorVersion::latest())
            .region(aws_config::Region::new(cfg.region.clone()))
            .load()
            .await;

        let firehose = matches!(cfg.delivery_mode, DeliveryMode::Firehose)
            .then(|| aws_sdk_firehose::Client::new(&sdk_cfg));
        let kinesis = matches!(cfg.delivery_mode, DeliveryMode::Kinesis)
            .then(|| aws_sdk_kinesis::Client::new(&sdk_cfg));
        let lambda = matches!(cfg.delivery_mode, DeliveryMode::Lambda)
            .then(|| aws_sdk_lambda::Client::new(&sdk_cfg));

        Ok(Self { cfg, firehose, kinesis, lambda })
    }

    pub async fn deliver(&self, chunk: &AudioChunk) -> Result<()> {
        let wav = encode_wav(&chunk.samples, chunk.channels, chunk.sample_rate);
        let key = format!(
            "{}-{}-{}",
            chunk.device_name.to_lowercase().replace(' ', "_"),
            chunk.channel_label,
            chunk.timestamp.format("%Y%m%dT%H%M%SZ"),
        );
        match self.cfg.delivery_mode {
            DeliveryMode::Firehose => self.put_firehose(&wav, chunk).await,
            DeliveryMode::Kinesis  => self.put_kinesis(&wav, &key, chunk).await,
            DeliveryMode::Lambda   => self.invoke_lambda(&wav, &key, chunk).await,
        }
    }

    // ── Kinesis Data Firehose ────────────────────────────────────────────────

    async fn put_firehose(&self, wav: &[u8], chunk: &AudioChunk) -> Result<()> {
        let cfg = self
            .cfg
            .firehose
            .as_ref()
            .ok_or_else(|| anyhow!("Missing firehose config"))?;
        let client = self.firehose.as_ref().unwrap();

        let stream = format!(
            "{}-{}-{}",
            cfg.stream_name_prefix,
            chunk.device_name.to_lowercase().replace(' ', "_"),
            chunk.channel_label,
        );

        client
            .put_record()
            .delivery_stream_name(&stream)
            .record(
                aws_sdk_firehose::types::Record::builder()
                    .data(aws_sdk_firehose::primitives::Blob::new(wav.to_vec()))
                    .build()?,
            )
            .send()
            .await
            .map_err(|e| anyhow!("Firehose PutRecord error: {e}"))?;

        tracing::info!("Firehose → {} ({} bytes)", stream, wav.len());
        Ok(())
    }

    // ── Kinesis Data Streams ─────────────────────────────────────────────────

    async fn put_kinesis(&self, wav: &[u8], key: &str, chunk: &AudioChunk) -> Result<()> {
        let cfg = self
            .cfg
            .kinesis
            .as_ref()
            .ok_or_else(|| anyhow!("Missing kinesis config"))?;
        let client = self.kinesis.as_ref().unwrap();

        let partition = format!("{}-{}", chunk.device_name, chunk.channel_label);

        client
            .put_record()
            .stream_name(&cfg.stream_name)
            .partition_key(&partition)
            .data(aws_sdk_kinesis::primitives::Blob::new(wav.to_vec()))
            .send()
            .await
            .map_err(|e| anyhow!("Kinesis PutRecord error: {e}"))?;

        tracing::info!(
            "Kinesis → {} partition={} key={} ({} bytes)",
            cfg.stream_name, partition, key, wav.len()
        );
        Ok(())
    }

    // ── Lambda ───────────────────────────────────────────────────────────────

    async fn invoke_lambda(&self, wav: &[u8], key: &str, chunk: &AudioChunk) -> Result<()> {
        let cfg = self
            .cfg
            .lambda
            .as_ref()
            .ok_or_else(|| anyhow!("Missing lambda config"))?;
        let client = self.lambda.as_ref().unwrap();

        let payload = serde_json::to_vec(&json!({
            "device":       chunk.device_name,
            "channel":      chunk.channel_label,
            "sample_rate":  chunk.sample_rate,
            "channels":     chunk.channels,
            "timestamp":    chunk.timestamp.to_rfc3339(),
            "key":          key,
            "audio_base64": BASE64_STANDARD.encode(wav),
            "format":       "wav",
        }))?;

        client
            .invoke()
            .function_name(&cfg.function_name)
            .payload(aws_sdk_lambda::primitives::Blob::new(payload))
            .send()
            .await
            .map_err(|e| anyhow!("Lambda Invoke error: {e}"))?;

        tracing::info!("Lambda → {} key={} ({} bytes wav)", cfg.function_name, key, wav.len());
        Ok(())
    }
}
