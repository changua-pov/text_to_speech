use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub aws: AwsConfig,
    pub devices: Vec<DeviceConfig>,
    pub recording: RecordingConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AwsConfig {
    pub region: String,
    #[serde(default)]
    pub delivery_mode: DeliveryMode,
    pub firehose: Option<FirehoseConfig>,
    pub kinesis: Option<KinesisConfig>,
    pub lambda: Option<LambdaConfig>,
}

#[derive(Debug, Clone, Deserialize, Serialize, Default, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum DeliveryMode {
    #[default]
    Firehose,
    Kinesis,
    Lambda,
}

/// One Firehose delivery stream per device+channel.
/// Stream name pattern: "{prefix}-{device}-{channel}"  e.g. "raspi-audio-mic0-left"
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FirehoseConfig {
    pub stream_name_prefix: String,
}

/// Single Kinesis Data Stream; partition key = "{device}-{channel}".
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct KinesisConfig {
    pub stream_name: String,
}

/// Single Lambda function invoked per chunk with a JSON + base64-WAV payload.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct LambdaConfig {
    pub function_name: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct DeviceConfig {
    /// Human-readable label used in stream/partition key names.
    pub name: String,
    /// ALSA device string, e.g. "hw:0,0" or "plughw:1,0".
    pub alsa_device: String,
    /// Number of channels: 1 = mono, 2 = stereo, etc.
    pub channels: u32,
    pub sample_rate: u32,
    /// When true and channels > 1, each channel is also stored as a separate mono recording.
    #[serde(default = "default_true")]
    pub split_channels: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RecordingConfig {
    /// Duration of each audio chunk sent to AWS (seconds).
    pub chunk_duration_secs: u64,
}

fn default_true() -> bool {
    true
}
