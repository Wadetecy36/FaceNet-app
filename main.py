import base64, requests, json

with open('F:/FaceNet/photo.jpg', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

resp = requests.post('http://localhost:8000/infer', json={
    'frame_b64': b64,
    'device_id': 'test-device',
    'session_id': 'session-001',
    'timestamp': 1741600000.0,
    'known_faces': [],
    'location': 'Main Entrance'
})

print(json.dumps(resp.json(), indent=2))