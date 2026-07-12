class WikiBaseException(Exception):
    """지식베이스 비즈니스 예외 최상위 클래스"""
    code = "INTERNAL_ERROR"
    status_code = 500

class InvalidArgumentException(WikiBaseException):
    """유효하지 않은 인수가 입력되었을 때 발생하는 예외"""
    code = "INVALID_ARGUMENT"
    status_code = 400

class DatabaseException(WikiBaseException):
    """데이터베이스 트랜잭션 또는 통신이 실패했을 때 발생하는 예외"""
    code = "DATABASE_TRANSACTION_FAILED"
    status_code = 503

class StorageOperationException(WikiBaseException):
    """R2/S3 스토리지 파일 입출력 또는 디렉토리 조작 실패 예외"""
    code = "STORAGE_IO_ERROR"
    status_code = 500
