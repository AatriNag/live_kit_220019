import asyncio
import logging
from typing import List, Dict, Set

logger = logging.getLogger("interruption_handler")


class InterruptionHandler:
    def __init__(self, ignored_fillers_config: Dict[str, List[str]], confidence_threshold: float = 0.6):
        self.ignored_fillers: Dict[str, Set[str]] = {
            lang: set(words) for lang, words in ignored_fillers_config.items()
        }
        self.confidence_threshold = confidence_threshold
        self.is_agent_speaking = False
        self.lock = asyncio.Lock()
        
        logger.info(f"Initialized with languages: {list(self.ignored_fillers.keys())}")
        for lang, words in self.ignored_fillers.items():
            logger.info(f"{lang.upper()}: {list(words)}")

    async def add_filler(self, lang: str, word: str) -> bool:
        async with self.lock:
            word_lower = word.lower().strip()
            if not word_lower:
                return False
            self.ignored_fillers.setdefault(lang, set()).add(word_lower)
            logger.info(f"Added filler '{word_lower}' for '{lang}'")
            return True

    async def remove_filler(self, lang: str, word: str) -> bool:
        async with self.lock:
            word_lower = word.lower().strip()
            if lang in self.ignored_fillers and word_lower in self.ignored_fillers[lang]:
                self.ignored_fillers[lang].remove(word_lower)
                logger.info(f"Removed filler '{word_lower}' for '{lang}'")
                return True
            return False

    def _normalize_language_code(self, lang: str) -> str:
        if not lang:
            return "en"
        return lang.split('-')[0].split('_')[0].lower().strip()

    def _get_fillers_for_language(self, lang: str) -> Set[str]:
        if lang in self.ignored_fillers:
            return self.ignored_fillers[lang]
        
        normalized = self._normalize_language_code(lang)
        if normalized in self.ignored_fillers:
            return self.ignored_fillers[normalized]
        
        return set()

    def update_agent_state(self, is_speaking: bool) -> None:
        was_speaking = self.is_agent_speaking
        self.is_agent_speaking = is_speaking
        
        if was_speaking != is_speaking:
            state = "SPEAKING" if is_speaking else "LISTENING"
            logger.info(f"Agent state: {state}")

    def should_interrupt(self, transcript: str, lang: str, confidence: float) -> bool:
        if confidence < self.confidence_threshold:
            logger.info(f"Low confidence: {transcript} ({confidence:.2f}) - IGNORE")
            return False

        transcript_clean = transcript.lower().strip()
        if not transcript_clean:
            return False

        words = transcript_clean.split()
        if not words:
            return False

        fillers = self._get_fillers_for_language(lang)
        
        if not fillers:
            logger.info(f"No fillers for '{lang}': {transcript} - ALLOW")
            return True

        non_filler_words = [w for w in words if w not in fillers]

        if not non_filler_words:
            # 'words' contains the list of matched fillers (e.g., ['um', 'uh'])
            logger.info(f"🔇 IGNORED INTERRUPTION | Matched fillers in '{lang}': {words} (from transcript: '{transcript}')")
            return False

        # We can also make this log more specific, showing *which* words were considered "real"
        logger.info(f"✅ VALID INTERRUPTION | Real speech in '{lang}': {non_filler_words} (from transcript: '{transcript}')")
        return True

    def get_statistics(self) -> Dict:
        return {
            "languages_configured": list(self.ignored_fillers.keys()),
            "total_filler_words": sum(len(words) for words in self.ignored_fillers.values()),
            "confidence_threshold": self.confidence_threshold,
            "is_agent_speaking": self.is_agent_speaking,
            "fillers_by_language": {
                lang: sorted(list(words)) for lang, words in self.ignored_fillers.items()
            }
        }