"""RunPod Serverless worker package for ibm-granite/granite-speech-4.1-2b."""

from workers.granite_speech.handler import GraniteSpeechRunpodHandler

__all__ = ["GraniteSpeechRunpodHandler"]
