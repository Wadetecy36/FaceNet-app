import * as faceapi from '@vladmandic/face-api';

const MODEL_URL = 'https://vladmandic.github.io/face-api/model/';

export class FaceService {
  private static initialized = false;

  static async loadModels() {
    if (this.initialized) return;
    
    await Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
      faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL),
      faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL),
    ]);
    
    this.initialized = true;
    console.log('Face-api models loaded');
  }

  static async detectFaces(video: HTMLVideoElement) {
    return faceapi
      .detectAllFaces(video, new faceapi.TinyFaceDetectorOptions())
      .withFaceLandmarks()
      .withFaceDescriptors();
  }

  static createMatcher(users: { name: string; descriptor: number[] }[]) {
    if (users.length === 0) return null;
    
    const labeledDescriptors = users.map(user => {
      const descriptor = new Float32Array(user.descriptor);
      return new faceapi.LabeledFaceDescriptors(user.name, [descriptor]);
    });
    
    return new faceapi.FaceMatcher(labeledDescriptors, 0.6);
  }
}
