# config.py
REPLICATE_API_TOKEN = "r8_XAMwIXB6Mk7AA6hZhHJpj88MHCQkobH3kUbNy"
TELEGRAM_BOT_TOKEN = "8508922699:AAGdzF45tisPQiaOHRpwXed4kPPaqLNciXQ"

# Список админов (int user_id). Пример: [123456789]
ADMIN_IDS = [7271120317]

# Настройки очереди: сколько параллельных задач обрабатывать (1 = строго последовательно)
WORKER_COUNT = 1

# Папки
INPUT_DIR = "inputs"
OUTPUT_DIR = "outputs"
LOG_DIR = "logs"

# Максимальное число вариантов, которые будем просить у модели (если нужно)
VARIANTS = 1

# Префикс имени файлов
FILE_PREFIX = "damage"

# Optional: ограничение по размеру фото (в байтах). 8MB по умолчанию
MAX_FILE_SIZE = 8 * 1024 * 1024
