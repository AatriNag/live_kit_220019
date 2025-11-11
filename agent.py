import logging
import os
import asyncio
from dotenv import load_dotenv
from typing import List, Dict

from livekit.plugins import deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    inference,
    metrics,
    UserInputTranscribedEvent,
    AgentStateChangedEvent,
)

from interruption_handler import InterruptionHandler

logger = logging.getLogger("agent")
load_dotenv(".env.local")


def _load_filler_config() -> Dict[str, List[str]]:
    filler_config = {}
    for key, value in os.environ.items():
        if key.startswith("FILLERS_"):
            try:
                lang_code = key.split("_")[1].lower()
                words = [word.strip() for word in value.split(",") if word.strip()]
                if words:
                    filler_config[lang_code] = words
                    logger.info(f"Loaded {len(words)} fillers for '{lang_code}'")
            except Exception as e:
                logger.error(f"Failed to parse {key}: {e}")
    return filler_config


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a multilingual voice AI assistant.

RULES:
1. Always respond in the SAME language as the user's most recent input
2. When user switches languages, immediately switch your response language
3. Be natural and conversational
4. Don't announce language switches unless asked

EXAMPLES:
User (English): "Hello, how are you?"
You (English): "Hello! I'm doing great. How can I help you?"

User (Spanish): "Ahora en español"
You (Spanish): "¡Por supuesto! ¿En qué puedo ayudarte?"

Make multilingual conversations feel effortless and natural.""",
        )
        
        filler_config = _load_filler_config()
        self.interruption_handler = InterruptionHandler(
            ignored_fillers_config=filler_config, 
            confidence_threshold=0.6, 
        )


async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        llm=inference.LLM(model="openai/gpt-4.1-mini"),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        ),
        stt = deepgram.STT(
            model="nova-3",              # or "nova-3" (multilingual model)
            language="multi",            # <— enables automatic multi-language detection
        ),
        turn_detection=MultilingualModel(),
        preemptive_generation=True,
        false_interruption_timeout=0.2,
    )

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)
    
    agent = Assistant()

    @session.on("user_input_transcribed")
    def on_user_input(event: UserInputTranscribedEvent):
        transcript = event.transcript.strip().lower()
        detected_lang = event.language or "en"
        
        # Get confidence from event if available, otherwise default to high confidence
        transcript_confidence = getattr(event, 'confidence', 0.9)

        logger.info(f"USER INPUT | Text: '{transcript}' | Lang: {detected_lang} | Confidence: {transcript_confidence:.2f} | Agent Speaking: {agent.interruption_handler.is_agent_speaking}")

        if agent.interruption_handler.is_agent_speaking:
            should_interrupt = agent.interruption_handler.should_interrupt(
                transcript=transcript,
                lang=detected_lang,
                confidence=transcript_confidence
            )

            if not should_interrupt:
                logger.info(f"🔇 IGNORED INTERRUPTION | Fillers only in '{detected_lang}': '{transcript}'")
                asyncio.create_task(session.suppress_interruption())
                return
            else:
                logger.info(f"✅ VALID INTERRUPTION | Real speech in '{detected_lang}': '{transcript}'")
                return
        else:
            logger.info(f"👂 AGENT LISTENING | Accepting all input in '{detected_lang}': '{transcript}'")
            return

    @session.on("agent_state_changed")
    def on_agent_state_change(event: AgentStateChangedEvent):
        logger.info(f"Agent state: {event.new_state}")
        is_speaking = event.new_state == "speaking"
        agent.interruption_handler.update_agent_state(is_speaking)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
