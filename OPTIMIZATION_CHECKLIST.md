# 🎯 ЧЕК-ЛИСТ ИСПРАВЛЕНИЙ И ОПТИМИЗАЦИЙ для bot.py

**Дата создания:** 23 января 2026  
**Общее время работы:** 15-20 часов  
**Приоритет:** ВЫСОКИЙ

---

## 🔴 КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ (НЕМЕДЛЕННО)

### [ИСПОЛЬНИТЬ СЕЙЧАС] ❌ BUG #1: Переменная repo_info вместо repo_data
**Файл:** bot.py  
**Строка:** 5648  
**Функция:** `show_users_management()`  
**Статус:** 🔴 КРИТИЧЕСКАЯ ОШИБКА  

```python
# НЕПРАВИЛЬНО (текущее состояние):
repo_url = repo_info.get('repo_url', 'не задан')  # NameError!

# ПРАВИЛЬНО (нужно исправить):
repo_url = repo_data.get('repo_url', 'не задан')
```

**Действие:**
- [ ] Найти строку 5648
- [ ] Заменить `repo_info` на `repo_data`
- [ ] Протестировать функцию администратором
- [ ] Проверить все переменные в цикле

**Время:** 5 минут  
**Риск:** Критический - функция вообще не работает  

---

## 🟠 ВЫСОКИЙ ПРИОРИТЕТ (НЕДЕЛЯ 1)

### [1 час] Удалить неиспользуемые переменные и функции

#### 1.1 Удалить переменную AIORGRAM_AVAILABLE
**Строка:** 2077
```python
# Удалить эту строку:
AIORGRAM_AVAILABLE = False
```
- [ ] Удалить строку 2077
- [ ] Проверить grep на использование (не должно быть)
- [ ] Проверить импорты aiogram (не нужны)

#### 1.2 Удалить переменную dp
**Строка:** 2091
```python
# Удалить эти строки:
class _StubDispatcher:
    def message(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

dp = _StubDispatcher()
```
- [ ] Удалить класс _StubDispatcher
- [ ] Удалить строку инициализации dp
- [ ] Проверить на использование dp в коде

#### 1.3 Удалить функцию migrate_user_repos_format()
**Строка:** 1337
```python
# Можно удалить эту функцию - она не вызывается
def migrate_user_repos_format() -> bool:
    ...
```
- [ ] Либо удалить полностью
- [ ] Либо переместить в `migration_tools.py` для ручного запуска
- [ ] Документировать в README если оставить

#### 1.4 Удалить/пересмотреть функцию apply_user_git_config()
**Строка:** 5681
- [ ] Проверить необходимость функции
- [ ] Если нужна - реализовать полностью
- [ ] Если не нужна - удалить

**Время:** 30 минут  
**Результат:** -10 строк кода, чище код  

---

### [4-5 часов] Создать класс LockManager для работы с блокировками

**Локация:** Вставить после класса `GitLabLFSManager` (около строки 880)

```python
class LockManager:
    """Unified interface for managing document locks via Git LFS"""
    
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.cache = {}
        self.cache_time = None
        self.cache_ttl = 30  # секунд
    
    def get_lock_info(self, file_path: str) -> dict:
        """Get lock information for a file (combined git lfs approach)"""
        # Реализация из get_lfs_lock_info()
        pass
    
    def is_locked(self, file_path: str) -> bool:
        """Check if file is locked"""
        lock_info = self.get_lock_info(file_path)
        return lock_info is not None
    
    def is_locked_by_user(self, file_path: str, user_id: int, git_username: str = None) -> bool:
        """Check if file is locked by specific user"""
        # Объединить логику проверки из 5 мест
        lock_info = self.get_lock_info(file_path)
        if not lock_info:
            return False
        
        lock_owner = lock_info.get('owner', '')
        return (
            lock_owner == str(user_id) or
            lock_owner == git_username or
            (git_username and lock_owner.lower() == git_username.lower())
        )
    
    def create_lock(self, file_path: str) -> bool:
        """Create lock for file"""
        try:
            subprocess.run(
                ["git", "lfs", "lock", file_path],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True
            )
            self._invalidate_cache()
            return True
        except subprocess.CalledProcessError:
            return False
    
    def remove_lock(self, file_path: str, force: bool = False) -> bool:
        """Remove lock from file"""
        try:
            lock_info = self.get_lock_info(file_path)
            if not lock_info:
                return True  # Already unlocked
            
            lock_id = lock_info.get('id')
            cmd = ["git", "lfs", "unlock"]
            if force:
                cmd.append("--force")
            if lock_id:
                cmd.extend(["--id", str(lock_id)])
            else:
                cmd.append(Path(file_path).name)
            
            subprocess.run(cmd, cwd=str(self.repo_path), check=True, capture_output=True)
            self._invalidate_cache()
            return True
        except subprocess.CalledProcessError:
            return False
    
    def get_all_locks(self) -> dict:
        """Get all locks in repository with caching"""
        if self._is_cache_valid():
            return self.cache
        
        try:
            result = subprocess.run(
                ["git", "lfs", "locks"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True
            )
            
            locks = {}
            for line in result.stdout.splitlines():
                if line.strip():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        path = parts[0]
                        owner = parts[1]
                        lock_id = parts[2] if len(parts) > 2 else None
                        locks[path] = {"owner": owner, "id": lock_id}
            
            self.cache = locks
            self.cache_time = time.time()
            return locks
        except subprocess.CalledProcessError:
            return {}
    
    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if self.cache_time is None:
            return False
        return time.time() - self.cache_time < self.cache_ttl
    
    def _invalidate_cache(self):
        """Invalidate cache"""
        self.cache = {}
        self.cache_time = None
```

**Что заменить:**
- [ ] Заменить все вызовы `get_lfs_lock_info()` на `lock_manager.get_lock_info()`
- [ ] Заменить все проверки блокировки на `lock_manager.is_locked_by_user()`
- [ ] Заменить команды `git lfs lock` на `lock_manager.create_lock()`
- [ ] Заменить команды `git lfs unlock` на `lock_manager.remove_lock()`

**Файлы для рефакторинга:**
- [ ] `handle_document_upload()` - удалить логику блокировки
- [ ] `lock_document_by_name()` - использовать lock_manager
- [ ] `unlock_document_by_name()` - использовать lock_manager
- [ ] `get_document_keyboard()` - использовать lock_manager

**Результат:** -200+ строк кода  
**Время:** 4-5 часов  

---

### [1-2 часа] Создать функцию find_document()

**Локация:** Добавить после функции `require_user_repo()` (около строки 1520)

```python
def is_valid_document(file_path: Path) -> bool:
    """Check if path is a valid document (not in hidden/system directories)"""
    if file_path.suffix.lower() != '.docx':
        return False
    
    parts = file_path.parts
    for part in parts:
        if part.startswith('.') or part in ['__pycache__', 'node_modules']:
            return False
    
    return True


def find_document(repo_root: Path, doc_name: str) -> Path:
    """Find document in repository, excluding hidden/system directories
    
    Args:
        repo_root: Repository root path
        doc_name: Document name to find
    
    Returns:
        Path to document or None if not found
    """
    for file_path in repo_root.rglob(doc_name):
        if is_valid_document(file_path):
            return file_path
    return None
```

**Что заменить:** Во всех 7 функциях заменить поиск документа на:
```python
doc_path = find_document(repo_root, doc_name)
if not doc_path or not doc_path.exists():
    # Handle error
    return
```

**Функции для обновления:**
- [ ] `handle_doc_selection()` - строка 2838
- [ ] `download_document()` - строка 2919
- [ ] `upload_changes()` - строка 2987
- [ ] `lock_document_by_name()` - строка 3741
- [ ] `unlock_document_by_name()` - строка 3692
- [ ] `handle_doc_name_input()` - строка 3146
- [ ] `get_document_keyboard()` - строка 2741

**Результат:** -56 строк кода, более чистый код  
**Время:** 1-2 часа  

---

## 🟡 СРЕДНИЙ ПРИОРИТЕТ (НЕДЕЛЯ 2)

### [2-3 часа] Создать функцию check_admin_rights()

**Локация:** Добавить после функции `check_rate_limit()` (около строки 47)

```python
def check_admin_rights(user_id: int) -> bool:
    """Check if user has admin rights"""
    try:
        return str(user_id) in ADMIN_IDS
    except (TypeError, ValueError):
        logging.warning(f"Error checking admin rights for user {user_id}")
        return False


async def require_admin(message) -> bool:
    """Ensure user is admin, send error message if not"""
    if not check_admin_rights(message.from_user.id):
        await message.answer(
            "❌ Только администраторы могут выполнить эту операцию.",
            reply_markup=get_main_keyboard(user_id=message.from_user.id)
        )
        return False
    return True
```

**Что заменить:** Во всех 5 функциях заменить проверку админа на:
```python
if not check_admin_rights(message.from_user.id):
    await message.answer("❌ Только админы...", reply_markup=...)
    return
```

**Функции для обновления:**
- [ ] `check_lock_status()` - строка 3937
- [ ] `force_unlock_request()` - строка 3886
- [ ] `fix_lfs_issues()` - строка 4142
- [ ] `resync_repository()` - строка 4626
- [ ] `commit_all_changes()` - строка 4299 (не всегда)

**Результат:** -20 строк кода  
**Время:** 2-3 часа  

---

### [3-4 часа] Создать класс SessionManager

**Локация:** Добавить после класса `VCSConfigurationManager` (около строка 1245)

```python
class SessionManager:
    """Centralized session management for all user interactions"""
    
    def __init__(self):
        self.doc_sessions = {}  # {user_id: {'doc': name, 'action': action}}
        self.config_state = {}  # {user_id: state}
        self.config_data = {}   # {user_id: data}
        self.edit_sessions = {} # {user_id: edit_session_data}
        self.rate_limit = {}    # {user_id: timestamp}
    
    def get_doc_session(self, user_id: int) -> dict:
        """Get document session for user"""
        return self.doc_sessions.get(user_id, {})
    
    def set_doc_session(self, user_id: int, data: dict):
        """Set document session for user"""
        self.doc_sessions[user_id] = data
    
    def clear_doc_session(self, user_id: int):
        """Clear document session"""
        self.doc_sessions.pop(user_id, None)
    
    def get_config_state(self, user_id: int) -> str:
        """Get configuration state"""
        return self.config_state.get(user_id, None)
    
    def set_config_state(self, user_id: int, state: str):
        """Set configuration state"""
        self.config_state[user_id] = state
    
    def get_config_data(self, user_id: int) -> dict:
        """Get configuration data"""
        return self.config_data.get(user_id, {})
    
    def set_config_data(self, user_id: int, data: dict):
        """Set configuration data"""
        self.config_data[user_id] = data
    
    def clear_all(self, user_id: int):
        """Clear all sessions for user"""
        self.doc_sessions.pop(user_id, None)
        self.config_state.pop(user_id, None)
        self.config_data.pop(user_id, None)
        self.edit_sessions.pop(user_id, None)
        self.rate_limit.pop(user_id, None)
    
    # ... остальные методы
```

**Что заменить:**
- [ ] Глобальные переменные через singleton
- [ ] `user_doc_sessions` → `session_manager.doc_sessions`
- [ ] `user_config_state` → `session_manager.get_config_state()`
- [ ] `globals().get('user_edit_sessions')` → `session_manager.edit_sessions`

**Результат:** Более чистый код, лучше управление сессиями  
**Время:** 3-4 часа  

---

## 🔵 НИЗКИЙ ПРИОРИТЕТ (НЕДЕЛЯ 3)

### [2.5 часа] Кэш для user_repos (TTL 60 секунд)

**Добавить класс:**

```python
class UserRepoCache:
    """Cache for user repositories with TTL"""
    
    def __init__(self, ttl=60):
        self.cache = {}
        self.timestamps = {}
        self.ttl = ttl
    
    def get(self, cache_key):
        if self._is_valid(cache_key):
            return self.cache[cache_key]
        return None
    
    def set(self, cache_key, value):
        self.cache[cache_key] = value
        self.timestamps[cache_key] = time.time()
    
    def _is_valid(self, cache_key):
        if cache_key not in self.timestamps:
            return False
        age = time.time() - self.timestamps[cache_key]
        return age < self.ttl
    
    def invalidate(self, cache_key=None):
        if cache_key:
            self.cache.pop(cache_key, None)
            self.timestamps.pop(cache_key, None)
        else:
            self.cache.clear()
            self.timestamps.clear()

# Create global instance
user_repo_cache = UserRepoCache(ttl=60)
```

**Что изменить в load_user_repos():**
```python
def load_user_repos() -> dict:
    global user_repos_cache
    
    # Check cache first (добавить новую проверку)
    cached = user_repo_cache.get('all_repos')
    if cached is not None:
        user_repos_cache = cached
        return cached
    
    # ... существующий код load ...
    
    # Add to cache
    user_repo_cache.set('all_repos', data)
    user_repos_cache = data
    return data
```

**Результат:** -80-90% IO операций на user interactions  
**Время:** 2.5 часа  

---

### [3.5 часа] Кэш для LFS locks (TTL 30 секунд)

```python
class LfsLockCache:
    """Cache for LFS locks with TTL"""
    
    def __init__(self, repo_path: Path, ttl=30):
        self.repo_path = repo_path
        self.cache = {}
        self.timestamp = None
        self.ttl = ttl
    
    def get_all_locks(self) -> dict:
        if self._is_valid():
            return self.cache.copy()
        
        locks = self._fetch_locks()
        self.cache = locks
        self.timestamp = time.time()
        return locks
    
    def _fetch_locks(self) -> dict:
        try:
            result = subprocess.run(
                ["git", "lfs", "locks"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=10
            )
            
            locks = {}
            for line in result.stdout.splitlines():
                if line.strip():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        path = parts[0]
                        owner = parts[1]
                        locks[path] = {"owner": owner}
            
            return locks
        except Exception as e:
            logging.error(f"Failed to fetch LFS locks: {e}")
            return {}
    
    def _is_valid(self) -> bool:
        if self.timestamp is None:
            return False
        return time.time() - self.timestamp < self.ttl
    
    def invalidate(self):
        self.cache = {}
        self.timestamp = None

# Использование:
# lfs_cache = LfsLockCache(repo_root)
# all_locks = lfs_cache.get_all_locks()  # Первый раз - fetch, потом - cache
```

**Результат:** Уменьшение subprocess вызовов с O(n) до O(1)  
**Время:** 3.5 часа  

---

## ✅ ПРОВЕРОЧНЫЙ СПИСОК

### До развертывания в production:

- [ ] Исправлена критическая ошибка (repo_info → repo_data)
- [ ] Удалены неиспользуемые переменные
- [ ] Все тесты проходят
- [ ] LockManager тестирован в обоих режимах (SSH и HTTPS)
- [ ] SessionManager работает стабильно
- [ ] Кэширование работает без проблем
- [ ] Логирование обновлено
- [ ] Документация обновлена
- [ ] Code review завершен

---

## 📊 ТРУДОЗАТРАТЫ ПО ПРИОРИТЕТАМ

| Категория | Часы | Важность |
|-----------|------|----------|
| 🔴 Критические исправления | 0.5 | СРОЧНО |
| 🟠 Удаление мертвого кода | 1 | ВЫСОКАЯ |
| 🟠 LockManager класс | 4.5 | ВЫСОКАЯ |
| 🟠 find_document функция | 1.5 | ВЫСОКАЯ |
| 🟡 check_admin_rights функция | 2.5 | СРЕДНЯЯ |
| 🟡 SessionManager класс | 3.5 | СРЕДНЯЯ |
| 🔵 Кэш user_repos | 2.5 | НИЗКАЯ |
| 🔵 Кэш LFS locks | 3.5 | НИЗКАЯ |
| **ИТОГО** | **19.5** | |

---

## 📋 ПОРЯДОК ВЫПОЛНЕНИЯ

1. **День 1 (3 часа):** Исправить критические ошибки + удалить мертвый код
2. **День 2-3 (8 часов):** Создать LockManager, find_document, check_admin_rights
3. **День 4 (5 часов):** SessionManager, обновить все вызовы
4. **День 5 (3-4 часа):** Кэширование + тестирование

---

## 🧪 ТЕСТИРОВАНИЕ

После каждого изменения:
- [ ] Запустить bot локально
- [ ] Тестировать пользовательские операции
- [ ] Проверить логи на ошибки
- [ ] Тестировать админ функции
- [ ] Проверить производительность

---

**Дата завершения:** По графику 5 дней  
**Ответственный:** [Укажите имя разработчика]  
**Статус:** ⏳ В ожидании начала работ
