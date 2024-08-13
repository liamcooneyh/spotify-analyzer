import os
import requests
import urllib.parse
from datetime import datetime
from flask import Flask, render_template, redirect, request, jsonify, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# SQLite setup for session storage
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///session_data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_SQLALCHEMY'] = SQLAlchemy(app)
app.config['SESSION_PERMANENT'] = False

db = app.config['SESSION_SQLALCHEMY']
Session(app)

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')

AUTH_URL = 'https://accounts.spotify.com/authorize'
TOKEN_URL = 'https://accounts.spotify.com/api/token'
API_BASE_URL = 'https://api.spotify.com/v1/'


@app.route('/')
def index():
    return "Welcome to my Spotify App <a href='/login'>Login with Spotify</a>"


@app.route('/login')
def login():
    scope = 'user-read-private user-read-email playlist-modify-public playlist-modify-private'
    
    params = {
        'client_id': CLIENT_ID,
        'response_type': 'code',
        'scope': scope,
        'redirect_uri': REDIRECT_URI,
        'show_dialog': True
    }
    
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)


@app.route('/callback')
def callback():
    if 'error' in request.args:
        return jsonify({"error": request.args['error']})
    
    if 'code' in request.args:
        req_body = {
            'code': request.args['code'],
            'grant_type': 'authorization_code',
            'redirect_uri': REDIRECT_URI,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        }
        
        response = requests.post(TOKEN_URL, data=req_body)
        token_info = response.json()
        
        session['access_token'] = token_info['access_token']
        session['refresh_token'] = token_info['refresh_token']
        session['expires_at'] = datetime.now().timestamp() + token_info['expires_in']
        
        return redirect('/playlist-creator')


@app.route('/playlist-creator')
def get_playlists():
    if 'access_token' not in session:
        return redirect('/login')
    
    if datetime.now().timestamp() > session['expires_at']:
        return redirect('/refresh-token')
    
    headers = {
        'Authorization': f"Bearer {session['access_token']}"
    }
    
    response = requests.get(API_BASE_URL + 'me/playlists', headers=headers)
    playlists = response.json().get('items', [])
    
    return render_template('index.html', playlists=playlists)


@app.route('/submit-playlists', methods=['POST'])
def submit_playlists():
    if 'access_token' not in session:
        return redirect('/login')
    
    if datetime.now().timestamp() > session['expires_at']:
        return redirect('/refresh-token')
    
    headers = {
        'Authorization': f"Bearer {session['access_token']}"
    }
    
    source_tracks = []
    selected_playlists = request.form.getlist('playlist')
    
    for playlist_id in selected_playlists:
        tracks_response = requests.get(API_BASE_URL + f'playlists/{playlist_id}/tracks', headers=headers)
        tracks = tracks_response.json()
        
        for track_item in tracks.get('items', []):
            track = track_item['track']
            track_id = track['id']
            track_name = track['name']
            artists = ", ".join([artist['name'] for artist in track['artists']])
            
            source_tracks.append({
                'track_id': track_id,
                'track_name': track_name,
                'artists': artists
            })

    # Get audio features for selected tracks
    combined_tracks = []

    for track in source_tracks:
        track_id = track['track_id']
        audio_features_response = requests.get(API_BASE_URL + f'audio-features/{track_id}', headers=headers)
        audio_features = audio_features_response.json()
        track.update({
            'danceability': audio_features.get('danceability'),
            'energy': audio_features.get('energy'),
            'key': audio_features.get('key'),
            'mode': audio_features.get('mode'),
            'tempo': audio_features.get('tempo')
        })
        combined_tracks.append(track)
        
    session['combined_tracks'] = combined_tracks
    return render_template('filter.html', tracks=combined_tracks)


@app.route('/apply-filters', methods=['POST'])
def apply_filters():
    combined_tracks = session.get('combined_tracks')

    # Set default values for the filter criteria if they are left blank
    min_danceability = float(request.form.get('min_danceability', 0.0)) if request.form.get('min_danceability') else 0.0
    max_danceability = float(request.form.get('max_danceability', 1.0)) if request.form.get('max_danceability') else 1.0
    min_energy = float(request.form.get('min_energy', 0.0)) if request.form.get('min_energy') else 0.0
    max_energy = float(request.form.get('max_energy', 1.0)) if request.form.get('max_energy') else 1.0
    min_tempo = float(request.form.get('min_tempo', 0.0)) if request.form.get('min_tempo') else 0.0
    max_tempo = float(request.form.get('max_tempo', float('inf'))) if request.form.get('max_tempo') else float('inf')
    
    key = request.form.get('key')
    key = int(key) if key else None

    # Filter tracks
    filtered_tracks = []

    for track in combined_tracks:
        if (
            min_danceability <= track['danceability'] <= max_danceability and
            min_energy <= track['energy'] <= max_energy and
            min_tempo <= track['tempo'] <= max_tempo and
            (track['key'] == key if key is not None else True)
        ):
            filtered_tracks.append(track)

    
    session['filtered_tracks'] = filtered_tracks
    return redirect('/review-playlist')


@app.route('/review-playlist')
def review_playlist():
    filtered_tracks = session.get('filtered_tracks', [])
    return render_template('playlist.html', tracks=filtered_tracks)


@app.route('/save-playlist', methods=['POST'])
def save_playlist():
    if 'access_token' not in session:
        return redirect('/login')
    
    if datetime.now().timestamp() > session['expires_at']:
        return redirect('/refresh-token')

    playlist_name = request.form.get('playlist_name')
    track_uris = [f"spotify:track:{track['track_id']}" for track in session.get('filtered_tracks', [])]

    headers = {
        'Authorization': f"Bearer {session['access_token']}",
        'Content-Type': 'application/json'
    }

    # Create a new playlist
    user_response = requests.get(API_BASE_URL + 'me', headers=headers)
    user_id = user_response.json()['id']

    playlist_data = {
        'name': playlist_name,
        'description': 'Playlist created using Blurb by Liam Cooney',
        'public': True  # or True, depending on your needs
    }

    create_playlist_response = requests.post(
        API_BASE_URL + f'users/{user_id}/playlists', 
        headers=headers, 
        json=playlist_data
    )

    if create_playlist_response.status_code != 201:
        return jsonify({"error": "Failed to create playlist"}), 400

    playlist_id = create_playlist_response.json()['id']
    playlist_url = create_playlist_response.json()['external_urls']['spotify']

    # Add tracks to the new playlist
    add_tracks_response = requests.post(
        API_BASE_URL + f'playlists/{playlist_id}/tracks',
        headers=headers,
        json={'uris': track_uris}
    )

    if add_tracks_response.status_code != 201:
        return jsonify({"error": "Failed to add tracks to playlist"}), 400

    return render_template('done.html', playlist_name=playlist_name, playlist_url=playlist_url)


@app.route('/refresh-token')
def refresh_token():
    if 'refresh_token' not in session:
        return redirect('/login')
    
    if datetime.now().timestamp() > session['expires_at']:
        req_body = {
            'grant_type': 'refresh_token',
            'refresh_token': session['refresh_token'],
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        }
    
        response = requests.post(TOKEN_URL, data=req_body)
        new_token_info = response.json()
        
        session['access_token'] = new_token_info['access_token']
        session['expires_at'] = datetime.now().timestamp() + new_token_info['expires_in']
        
    return redirect('/playlist-creator')

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
