/// Encodes raw PCM i16 samples into an in-memory WAV file (16-bit little-endian).
pub fn encode_wav(samples: &[i16], channels: u16, sample_rate: u32) -> Vec<u8> {
    let data_bytes = (samples.len() * 2) as u32;
    let mut buf = Vec::with_capacity((44 + data_bytes) as usize);

    // RIFF chunk descriptor
    buf.extend_from_slice(b"RIFF");
    buf.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    buf.extend_from_slice(b"WAVE");

    // fmt sub-chunk
    buf.extend_from_slice(b"fmt ");
    buf.extend_from_slice(&16u32.to_le_bytes());                               // sub-chunk size
    buf.extend_from_slice(&1u16.to_le_bytes());                                // AudioFormat: PCM
    buf.extend_from_slice(&channels.to_le_bytes());                            // NumChannels
    buf.extend_from_slice(&sample_rate.to_le_bytes());                         // SampleRate
    buf.extend_from_slice(&(sample_rate * channels as u32 * 2).to_le_bytes()); // ByteRate
    buf.extend_from_slice(&(channels * 2).to_le_bytes());                      // BlockAlign
    buf.extend_from_slice(&16u16.to_le_bytes());                               // BitsPerSample

    // data sub-chunk
    buf.extend_from_slice(b"data");
    buf.extend_from_slice(&data_bytes.to_le_bytes());
    for &s in samples {
        buf.extend_from_slice(&s.to_le_bytes());
    }

    buf
}
