from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from google.cloud import texttospeech
import io
import os
import tempfile
import uuid
from datetime import datetime, timedelta
import threading
import time
import re
import unicodedata

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# Initialize Google Cloud Text-to-Speech client
# Make sure you have set up Google Cloud credentials
client = texttospeech.TextToSpeechClient()

# Store for temporary audio files (cleanup after 1 hour)
audio_store = {}

def clean_text_for_tts(text):
    """
    Clean text for TTS by removing or replacing problematic characters
    
    Args:
        text (str): Raw text input
        
    Returns:
        str: Cleaned text suitable for TTS
    """
    if not text:
        return ""
    
    # Remove emojis and other pictographs
    # This covers most emoji ranges in Unicode
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
        "]+", flags=re.UNICODE
    )
    text = emoji_pattern.sub('', text)
    
    # Remove or replace special characters that might cause TTS issues
    replacements = {
        # Common symbols that should be spoken
        '&': ' and ',
        '@': ' at ',
        '#': ' hashtag ',
        '$': ' dollar ',
        '%': ' percent ',
        '+': ' plus ',
        '=': ' equals ',
        '<': ' less than ',
        '>': ' greater than ',
        '|': ' ',
        '\\': ' ',
        '/': ' slash ',
        '*': '',
        '^': '',
        '~': '',
        '`': '',
        '_': ' ',
        '{': '',
        '}': '',
        '[': '',
        ']': '',
        
        # Multiple punctuation cleanup
        '...': '.',
        '!!': '!',
        '??': '?',
        ';;': ';',
        '::': ':',
        
        # Common internet slang replacements
        'lol': 'laugh out loud',
        'LOL': 'laugh out loud',
        'omg': 'oh my god',
        'OMG': 'oh my god',
        'btw': 'by the way',
        'BTW': 'by the way',
        'fyi': 'for your information',
        'FYI': 'for your information',
    }
    
    # Apply replacements
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Remove URLs (basic pattern)
    url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
    text = url_pattern.sub(' ', text)
    
    # Remove email addresses
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    text = email_pattern.sub(' ', text)
    
    # Remove phone numbers (basic pattern)
    phone_pattern = re.compile(r'(\+?1[-.\s]?)?(\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4})')
    text = phone_pattern.sub(' phone number ', text)
    
    # Remove excessive whitespace and normalize
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    # Remove control characters
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C')
    
    # Ensure text ends with proper punctuation for natural speech
    if text and text[-1] not in '.!?':
        text += '.'
    
    return text

def validate_and_clean_text(text, max_length=5000):
    """
    Validate and clean text for TTS processing
    
    Args:
        text (str): Input text
        max_length (int): Maximum allowed text length
        
    Returns:
        tuple: (cleaned_text, error_message)
    """
    if not text:
        return None, "Text is required"
    
    if not isinstance(text, str):
        return None, "Text must be a string"
    
    # Clean the text
    cleaned_text = clean_text_for_tts(text)
    
    if not cleaned_text.strip():
        return None, "Text is empty after cleaning (contains only unsupported characters)"
    
    if len(cleaned_text) > max_length:
        return None, f"Text too long. Maximum {max_length} characters allowed, got {len(cleaned_text)}"
    
    return cleaned_text, None

def cleanup_old_files():
    """Clean up audio files older than 1 hour"""
    while True:
        current_time = datetime.now()
        to_remove = []
        
        for file_id, file_info in audio_store.items():
            if current_time - file_info['created'] > timedelta(hours=1):
                try:
                    os.unlink(file_info['path'])
                    to_remove.append(file_id)
                except:
                    pass
        
        for file_id in to_remove:
            audio_store.pop(file_id, None)
        
        time.sleep(3600)  # Check every hour

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "text-to-speech"})

@app.route('/clean-text', methods=['POST'])
def clean_text_endpoint():
    """
    Test endpoint to see how text will be cleaned
    
    Expected JSON payload:
    {
        "text": "Text to clean 😊🎉 #hashtag @mention"
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'text' not in data:
            return jsonify({"error": "Text is required"}), 400
        
        original_text = data['text']
        cleaned_text, error = validate_and_clean_text(original_text)
        
        if error:
            return jsonify({"error": error}), 400
        
        return jsonify({
            "original_text": original_text,
            "cleaned_text": cleaned_text,
            "original_length": len(original_text),
            "cleaned_length": len(cleaned_text),
            "characters_removed": len(original_text) - len(cleaned_text)
        })
        
    except Exception as e:
        print(f"Error in clean_text: {str(e)}")
        return jsonify({"error": f"Text cleaning failed: {str(e)}"}), 500

@app.route('/synthesize', methods=['POST'])
def synthesize_speech():
    """
    Convert text to speech using Google Cloud Text-to-Speech
    
    Expected JSON payload:
    {
        "text": "Text to convert to speech",
        "language_code": "en-US" (optional, default: "en-US"),
        "voice_name": "en-US-Wavenet-D" (optional),
        "speaking_rate": 1.0 (optional, 0.25 to 4.0),
        "pitch": 0.0 (optional, -20.0 to 20.0),
        "volume_gain_db": 0.0 (optional, -96.0 to 16.0),
        "skip_cleaning": false (optional, set to true to skip text cleaning)
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'text' not in data:
            return jsonify({"error": "Text is required"}), 400
        
        original_text = data['text']
        skip_cleaning = data.get('skip_cleaning', False)
        
        # Clean and validate text unless explicitly skipped
        if skip_cleaning:
            text = original_text
        else:
            text, error = validate_and_clean_text(original_text)
            if error:
                return jsonify({"error": error}), 400
        
        # Configuration parameters with defaults
        language_code = data.get('language_code', 'es-US')
        voice_name = data.get('voice_name', 'es-US-Chirp-HD-O')
        speaking_rate = max(0.25, min(4.0, data.get('speaking_rate', 1.0)))
        pitch = max(-20.0, min(20.0, data.get('pitch', 0.0)))
        volume_gain_db = max(-96.0, min(16.0, data.get('volume_gain_db', 0.0)))
        
        # Set the text input to be synthesized
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        # Build the voice request
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name
        )
        
        # Select the type of audio file
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
            pitch=pitch,
            volume_gain_db=volume_gain_db
        )
        
        # Perform the text-to-speech request
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        temp_file.write(response.audio_content)
        temp_file.close()
        
        # Store file info
        audio_store[file_id] = {
            'path': temp_file.name,
            'created': datetime.now(),
            'text': text[:100] + '...' if len(text) > 100 else text,
            'original_text': original_text[:100] + '...' if len(original_text) > 100 else original_text
        }
        
        return jsonify({
            "success": True,
            "file_id": file_id,
            "audio_url": f"/audio/{file_id}",
            "text_preview": text[:100] + '...' if len(text) > 100 else text,
            "original_text_preview": original_text[:100] + '...' if len(original_text) > 100 else original_text,
            "text_was_cleaned": not skip_cleaning,
            "voice_config": {
                "language_code": language_code,
                "voice_name": voice_name,
                "speaking_rate": speaking_rate,
                "pitch": pitch,
                "volume_gain_db": volume_gain_db
            }
        })
        
    except Exception as e:
        print(f"Error in synthesize_speech: {str(e)}")
        return jsonify({"error": f"Speech synthesis failed: {str(e)}"}), 500

@app.route('/audio/<file_id>', methods=['GET'])
def get_audio(file_id):
    """Serve audio file by ID"""
    try:
        if file_id not in audio_store:
            return jsonify({"error": "Audio file not found"}), 404
        
        file_path = audio_store[file_id]['path']
        
        if not os.path.exists(file_path):
            # Clean up dead reference
            audio_store.pop(file_id, None)
            return jsonify({"error": "Audio file not found"}), 404
        
        return send_file(
            file_path,
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name=f'speech_{file_id}.mp3'
        )
        
    except Exception as e:
        print(f"Error serving audio: {str(e)}")
        return jsonify({"error": "Failed to serve audio file"}), 500

@app.route('/voices', methods=['GET'])
def list_voices():
    """List available voices for a language"""
    try:
        language_code = request.args.get('language_code', 'en-US')
        
        # List available voices
        voices = client.list_voices(language_code=language_code)
        
        voice_list = []
        for voice in voices.voices:
            voice_list.append({
                "name": voice.name,
                "language_codes": list(voice.language_codes),
                "ssml_gender": voice.ssml_gender.name,
                "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
            })
        
        return jsonify({
            "voices": voice_list,
            "total_count": len(voice_list),
            "language_code": language_code
        })
        
    except Exception as e:
        print(f"Error listing voices: {str(e)}")
        return jsonify({"error": f"Failed to list voices: {str(e)}"}), 500

@app.route('/quick-speech', methods=['POST'])
def quick_speech():
    """
    Quick text-to-speech that returns audio directly without storing
    Good for short texts and immediate playback
    """
    try:
        data = request.get_json()
        
        if not data or 'text' not in data:
            return jsonify({"error": "Text is required"}), 400
        
        original_text = data['text']
        skip_cleaning = data.get('skip_cleaning', False)
        
        # Clean and validate text unless explicitly skipped
        if skip_cleaning:
            text = original_text
        else:
            text, error = validate_and_clean_text(original_text)
            if error:
                return jsonify({"error": error}), 400
        
        # Use default voice settings for quick speech
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code='es-US',
            name='es-US-Chirp-HD-O'
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # Return audio directly
        return send_file(
            io.BytesIO(response.audio_content),
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='quick_speech.mp3'
        )
        
    except Exception as e:
        print(f"Error in quick_speech: {str(e)}")
        return jsonify({"error": f"Quick speech failed: {str(e)}"}), 500

if __name__ == '__main__':
    # Check if Google Cloud credentials are set
    if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        print("Warning: GOOGLE_APPLICATION_CREDENTIALS not set")
        print("Make sure to set up Google Cloud authentication")
    
    print("Starting Text-to-Speech service...")
    print("Available endpoints:")
    print("  POST /synthesize - Convert text to speech")
    print("  GET  /audio/<id> - Get audio file")
    print("  POST /quick-speech - Quick text-to-speech")
    print("  POST /clean-text - Test text cleaning")
    print("  GET  /voices - List available voices")
    print("  GET  /health - Health check")
    
    app.run(host='0.0.0.0', port=8080, debug=True)