from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import os
import json
import google.generativeai as genai
import googleapiclient.discovery
import googleapiclient.errors
import logging
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import html

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure API Keys - Get from environment or App Service Configuration
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Initialize YouTube API client
youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

def extract_video_id(youtube_url):
    """Extract video ID from YouTube URL."""
    if "youtu.be" in youtube_url:
        # Handle shortened URL format
        return youtube_url.split("/")[-1].split("?")[0]
    elif "youtube.com/watch" in youtube_url:
        # Handle standard URL format
        import urllib.parse
        parsed_url = urllib.parse.urlparse(youtube_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        if 'v' in query_params:
            return query_params['v'][0]
        else:
            raise ValueError("Invalid YouTube URL: missing video ID parameter")
    else:
        raise ValueError("Invalid YouTube URL format. Please use a standard YouTube URL")

def get_video_info(video_id):
    """Get video information from YouTube API."""
    try:
        request = youtube.videos().list(
            part="snippet,contentDetails",
            id=video_id
        )
        response = request.execute()
        
        if not response['items']:
            return {"status": "error", "message": "Video not found. Please check the URL and try again."}
        
        video_info = response['items'][0]
        title = video_info['snippet']['title']
        description = video_info['snippet']['description']
        
        return {
            "status": "success",
            "title": title,
            "description": description,
            "video_id": video_id
        }
    except googleapiclient.errors.HttpError as e:
        logger.error(f"YouTube API error: {str(e)}")
        return {"status": "error", "message": f"YouTube API error: {str(e)}"}
    except Exception as e:
        logger.error(f"Error getting video info: {str(e)}")
        return {"status": "error", "message": str(e)}

def get_video_transcript(video_id):
    """Get transcript for a YouTube video."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try to get English transcript first
        try:
            transcript = transcript_list.find_transcript(['en'])
        except:
            # If English not available, get the first available transcript
            try:
                transcript = transcript_list.find_transcript(['en-US', 'en-GB'])
            except:
                # If no English variants, get the first transcript and translate it
                try:
                    transcript = transcript_list[0]
                    transcript = transcript.translate('en')
                except IndexError:
                    return {"status": "error", "message": "No transcript available for this video. Please choose a video with captions."}
            
        transcript_data = transcript.fetch()
        
        # Combine all text parts into one transcript
        full_transcript = ' '.join([part['text'] for part in transcript_data])
        
        return {
            "status": "success",
            "text": full_transcript
        }
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        logger.error(f"No transcript available: {str(e)}")
        return {"status": "error", "message": "No transcript available for this video. Please choose a video with captions."}
    except Exception as e:
        logger.error(f"Error getting transcript: {str(e)}")
        return {"status": "error", "message": f"Error retrieving transcript: {str(e)}"}

def get_captions_from_youtube_api(video_id):
    """Get captions using YouTube Data API as a fallback method."""
    try:
        # First, list available caption tracks
        captions_request = youtube.captions().list(
            part="snippet",
            videoId=video_id
        )
        captions_response = captions_request.execute()
        
        caption_tracks = captions_response.get('items', [])
        if not caption_tracks:
            logger.warning(f"No caption tracks found for video {video_id}")
            return {"status": "error", "message": "No captions available for this video"}
        
        # Prefer English captions
        caption_id = None
        for track in caption_tracks:
            lang = track['snippet']['language']
            if lang in ['en', 'en-US', 'en-GB']:
                caption_id = track['id']
                break
        
        # If no English captions, use the first available
        if not caption_id and caption_tracks:
            caption_id = caption_tracks[0]['id']
        
        if not caption_id:
            return {"status": "error", "message": "No usable captions found"}
        
        # Download the caption track
        caption_request = youtube.captions().download(
            id=caption_id,
            tfmt='srt'
        )
        
        # Execute request and get the response as bytes
        caption_data = caption_request.execute()
        
        # Convert from bytes to string if needed
        if isinstance(caption_data, bytes):
            caption_text = caption_data.decode('utf-8')
        else:
            caption_text = caption_data
            
        # Clean up the SRT format to get plain text
        cleaned_text = clean_srt_to_plain_text(caption_text)
        
        return {
            "status": "success",
            "text": cleaned_text
        }
    except googleapiclient.errors.HttpError as e:
        error_content = json.loads(e.content.decode('utf-8'))
        error_reason = error_content.get('error', {}).get('errors', [{}])[0].get('reason', '')
        
        if error_reason == 'forbidden':
            logger.error(f"Permission denied when accessing captions: {str(e)}")
            return {"status": "error", "message": "Unable to access captions due to permission restrictions"}
        else:
            logger.error(f"YouTube API error: {str(e)}")
            return {"status": "error", "message": f"YouTube API error: {str(e)}"}
    except Exception as e:
        logger.error(f"Error getting captions: {str(e)}")
        return {"status": "error", "message": f"Error retrieving captions: {str(e)}"}

def clean_srt_to_plain_text(srt_content):
    """Convert SRT format to plain text."""
    lines = srt_content.split('\n')
    plain_text = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Skip empty lines
        if not line:
            i += 1
            continue
        
        # Skip line numbers (digits only)
        if line.isdigit():
            i += 1
            continue
        
        # Skip timestamp lines (containing -->)
        if '-->' in line:
            i += 1
            continue
        
        # Add content lines
        if line:
            # Remove HTML tags if present
            plain_text.append(html.unescape(line))
        
        i += 1
    
    return ' '.join(plain_text)

def generate_quiz(transcript, video_title, num_questions=5):
    """Generate a quiz using Gemini API."""
    try:
        logger.info(f"Generating quiz for: {video_title}")
        
        # Setup Gemini model
        generation_model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Craft prompt for Gemini
        prompt = f"""
        Generate a quiz based on the following transcript from the YouTube video titled "{video_title}".
        
        TRANSCRIPT:
        {transcript}
        
        Create {num_questions} multiple-choice questions with 4 options each.
        
        Format the response as a JSON array with the following structure for each question:
        {{
            "question": "The question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": "The correct option (A, B, C, or D)",
            "explanation": "Explanation of why this is the correct answer"
        }}
        
        Only return valid JSON. No additional text before or after the JSON.
        """
        
        # Generate quiz with Gemini
        response = generation_model.generate_content(prompt)
        
        # Parse the response
        try:
            # Try to extract JSON from the response
            response_text = response.text
            
            # Clean up the response to ensure it's valid JSON
            # Look for JSON array beginning and ending
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1
            
            if start_idx != -1 and end_idx != -1:
                json_str = response_text[start_idx:end_idx]
                quiz_data = json.loads(json_str)
                return {"status": "success", "quiz": quiz_data}
            else:
                logger.error("Could not find JSON array in response")
                return {"status": "error", "message": "Invalid response format from Gemini API"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Gemini response: {str(e)}")
            return {"status": "error", "message": "Failed to parse quiz data"}
    except Exception as e:
        logger.error(f"Error generating quiz: {str(e)}")
        return {"status": "error", "message": str(e)}

def generate_quiz_from_description(description, video_title, num_questions=5):
    """Generate a quiz from video description when transcript is not available."""
    try:
        logger.info(f"Generating quiz from description for: {video_title}")
        
        # Setup Gemini model
        generation_model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Craft prompt for Gemini
        prompt = f"""
        Generate a quiz based on the following YouTube video title and description:
        
        TITLE: {video_title}
        
        DESCRIPTION:
        {description}
        
        Create {num_questions} multiple-choice questions with 4 options each.
        
        Format the response as a JSON array with the following structure for each question:
        {{
            "question": "The question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": "The correct option (A, B, C, or D)",
            "explanation": "Explanation of why this is the correct answer"
        }}
        
        Only return valid JSON. No additional text before or after the JSON.
        """
        
        # Generate quiz with Gemini
        response = generation_model.generate_content(prompt)
        
        # Parse the response
        try:
            # Try to extract JSON from the response
            response_text = response.text
            
            # Clean up the response to ensure it's valid JSON
            # Look for JSON array beginning and ending
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1
            
            if start_idx != -1 and end_idx != -1:
                json_str = response_text[start_idx:end_idx]
                quiz_data = json.loads(json_str)
                return {"status": "success", "quiz": quiz_data}
            else:
                logger.error("Could not find JSON array in response")
                return {"status": "error", "message": "Invalid response format from Gemini API"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Gemini response: {str(e)}")
            return {"status": "error", "message": "Failed to parse quiz data"}
    except Exception as e:
        logger.error(f"Error generating quiz: {str(e)}")
        return {"status": "error", "message": str(e)}

def get_message_from_gemini(message_prompt="Tell me something interesting about YouTube"):
    """Generate a message using Gemini API."""
    try:
        logger.info(f"Generating message with prompt: {message_prompt}")
        
        # Setup Gemini model
        generation_model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Generate message with Gemini
        response = generation_model.generate_content(message_prompt)
        
        return {
            "status": "success", 
            "message": response.text
        }
    except Exception as e:
        logger.error(f"Error generating message: {str(e)}")
        return {"status": "error", "message": f"Error generating message: {str(e)}"}

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/get-msg', methods=['GET'])
def get_message():
    """Return a simple message."""
    return jsonify({
        "status": "success",
        "message": "Welcome to the YouTube Quiz Generator! This API helps you create quizzes from YouTube videos."
    })

@app.route('/generate-quiz', methods=['POST'])
def process_youtube_video():
    """Process YouTube video and generate quiz."""
    data = request.json
    youtube_url = data.get('youtube_url')
    num_questions = int(data.get('num_questions', 5))
    
    if not youtube_url:
        return jsonify({"status": "error", "message": "YouTube URL is required"}), 400
    
    try:
        # Extract video ID from URL
        video_id = extract_video_id(youtube_url)
        
        # Get video information
        video_info = get_video_info(video_id)
        if video_info["status"] == "error":
            return jsonify(video_info), 500
        
        # Try to get transcript using youtube_transcript_api first
        transcript_result = get_video_transcript(video_id)
        
        # If transcript is not available, try using the YouTube Data API as fallback
        if transcript_result["status"] == "error":
            logger.info(f"Primary transcript method failed, trying fallback method")
            transcript_result = get_captions_from_youtube_api(video_id)
        
        # If transcript is still not available, use the video description
        if transcript_result["status"] == "success":
            # Generate quiz from transcript
            quiz_result = generate_quiz(
                transcript_result["text"], 
                video_info["title"],
                num_questions
            )
            content_source = "transcript"
        else:
            # Generate quiz from description as a last resort
            logger.info(f"No transcript available, using video description for quiz generation")
            quiz_result = generate_quiz_from_description(
                video_info["description"],
                video_info["title"],
                num_questions
            )
            content_source = "description"
        
        if quiz_result["status"] == "error":
            return jsonify(quiz_result), 500
        
        return jsonify({
            "status": "success",
            "video_title": video_info["title"],
            "quiz": quiz_result["quiz"],
            "content_source": content_source
        })
    
    except ValueError as e:
        logger.error(f"Invalid URL format: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Use environment variable for port or default to 5000
    port = int(os.environ.get('PORT', 5000))
    # Don't use debug mode in production
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=is_debug)