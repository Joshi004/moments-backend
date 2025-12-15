"""
Custom exception classes for the Video Moments application.
These exceptions provide meaningful error messages and HTTP status codes.
"""


class VideoMomentsException(Exception):
    """Base exception for all application errors."""
    
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class VideoNotFoundException(VideoMomentsException):
    """Raised when a video is not found."""
    
    def __init__(self, video_id: str):
        super().__init__(
            message=f"Video not found: {video_id}",
            status_code=404
        )
        self.video_id = video_id


class TranscriptNotFoundException(VideoMomentsException):
    """Raised when a transcript doesn't exist."""
    
    def __init__(self, video_id: str):
        super().__init__(
            message=f"Transcript not found for video: {video_id}",
            status_code=404
        )
        self.video_id = video_id


class MomentNotFoundException(VideoMomentsException):
    """Raised when a moment is not found."""
    
    def __init__(self, moment_id: str):
        super().__init__(
            message=f"Moment not found: {moment_id}",
            status_code=404
        )
        self.moment_id = moment_id


class AudioNotFoundException(VideoMomentsException):
    """Raised when audio file doesn't exist."""
    
    def __init__(self, video_id: str):
        super().__init__(
            message=f"Audio not found for video: {video_id}",
            status_code=404
        )
        self.video_id = video_id


class ClipNotFoundException(VideoMomentsException):
    """Raised when a video clip doesn't exist."""
    
    def __init__(self, moment_id: str):
        super().__init__(
            message=f"Video clip not found for moment: {moment_id}",
            status_code=404
        )
        self.moment_id = moment_id


class ProcessingInProgressException(VideoMomentsException):
    """Raised when trying to start a job that's already running."""
    
    def __init__(self, job_type: str, video_id: str):
        super().__init__(
            message=f"{job_type} already in progress for video: {video_id}",
            status_code=409  # Conflict
        )
        self.job_type = job_type
        self.video_id = video_id


class JobNotFoundException(VideoMomentsException):
    """Raised when a job is not found."""
    
    def __init__(self, job_type: str, video_id: str):
        super().__init__(
            message=f"No {job_type} job found for video: {video_id}",
            status_code=404
        )
        self.job_type = job_type
        self.video_id = video_id


class AIModelException(VideoMomentsException):
    """Raised when AI model call fails."""
    
    def __init__(self, model: str, error: str):
        super().__init__(
            message=f"AI model '{model}' error: {error}",
            status_code=502  # Bad Gateway
        )
        self.model = model
        self.error = error


class SSHTunnelException(VideoMomentsException):
    """Raised when SSH tunnel operations fail."""
    
    def __init__(self, service: str, error: str):
        super().__init__(
            message=f"SSH tunnel error for {service}: {error}",
            status_code=503  # Service Unavailable
        )
        self.service = service
        self.error = error


class ValidationException(VideoMomentsException):
    """Raised when request data validation fails."""
    
    def __init__(self, message: str):
        super().__init__(
            message=f"Validation error: {message}",
            status_code=400  # Bad Request
        )


class FileOperationException(VideoMomentsException):
    """Raised when file operations fail."""
    
    def __init__(self, operation: str, path: str, error: str):
        super().__init__(
            message=f"File {operation} failed for {path}: {error}",
            status_code=500
        )
        self.operation = operation
        self.path = path
        self.error = error


class VideoProcessingException(VideoMomentsException):
    """Raised when video processing (clipping, encoding) fails."""
    
    def __init__(self, operation: str, error: str):
        super().__init__(
            message=f"Video processing error during {operation}: {error}",
            status_code=500
        )
        self.operation = operation
        self.error = error


class AudioProcessingException(VideoMomentsException):
    """Raised when audio extraction fails."""
    
    def __init__(self, video_id: str, error: str):
        super().__init__(
            message=f"Audio extraction failed for {video_id}: {error}",
            status_code=500
        )
        self.video_id = video_id
        self.error = error


class TranscriptionException(VideoMomentsException):
    """Raised when transcription service fails."""
    
    def __init__(self, video_id: str, error: str):
        super().__init__(
            message=f"Transcription failed for {video_id}: {error}",
            status_code=500
        )
        self.video_id = video_id
        self.error = error

