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

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# Initialize Google Cloud Text-to-Speech client
# Make sure you have set up Google Cloud credentials
client = texttospeech.TextToSpeechClient()

# Store for temporary audio files (cleanup after 1 hour)
audio_store = {}

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
        "volume_gain_db": 0.0 (optional, -96.0 to 16.0)
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'text' not in data:
            return jsonify({"error": "Text is required"}), 400
        
        text = data['text']
        if not text.strip():
            return jsonify({"error": "Text cannot be empty"}), 400
        
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
            'text': text[:100] + '...' if len(text) > 100 else text
        }
        
        return jsonify({
            "success": True,
            "file_id": file_id,
            "audio_url": f"/audio/{file_id}",
            "text_preview": text[:100] + '...' if len(text) > 100 else text,
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
        
        text = data['text']
        if not text.strip():
            return jsonify({"error": "Text cannot be empty"}), 400
        
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
    print("  GET  /voices - List available voices")
    print("  GET  /health - Health check")
    
    app.run(host='0.0.0.0', port=8080, debug=True)