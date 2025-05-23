import copy

import spacy
from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
)
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngine, NlpEngineProvider
from spacy.cli import download  # type: ignore

from .ner_mapping import NERConfig
from .predefined_recognizers import _get_predefined_recognizers
from .predefined_recognizers.zh import CustomPatternRecognizer
from .regex_patterns import RegexPattern
from .transformers_recognizer import TransformersRecognizer


def _add_recognizers(
    registry: RecognizerRegistry,
    regex_groups: list[RegexPattern],
    custom_names: list[str],
    supported_languages: list[str] = ["en"],
) -> RecognizerRegistry:
    """
    Create a RecognizerRegistry and populate it with regex patterns and custom names.

    Parameters:
        regex_groups: List of regex patterns.
        custom_names: List of custom names to recognize.

    Returns:
        RecognizerRegistry: A RecognizerRegistry object loaded with regex and custom name recognizers.
    """

    for language in supported_languages:
        # custom recognizer per language
        if len(custom_names) > 0:
            custom_recognizer = PatternRecognizer

            if language == "zh":
                custom_recognizer = CustomPatternRecognizer

            registry.add_recognizer(
                custom_recognizer(
                    supported_entity="CUSTOM",
                    supported_language=language,
                    deny_list=custom_names,
                )
            )

        # predefined recognizers per language
        for _Recognizer in _get_predefined_recognizers(language):
            registry.add_recognizer(_Recognizer(supported_language=language))

    for pattern_data in regex_groups:
        languages = pattern_data["languages"] or ["en"]
        label = pattern_data["name"]
        reuse = pattern_data.get("reuse", False)

        patterns: list[Pattern] = list(
            map(
                lambda exp: Pattern(name=label, regex=exp, score=pattern_data["score"]),
                pattern_data.get("expressions", []) or [],
            )
        )

        for language in languages:
            if language not in supported_languages:
                continue

            if isinstance(reuse, dict):
                new_recognizer = copy.deepcopy(
                    registry.get_recognizers(language=reuse["language"], entities=[reuse["name"]])[
                        0
                    ]
                )
                new_recognizer.supported_language = language
                registry.add_recognizer(new_recognizer)
            else:
                registry.add_recognizer(
                    PatternRecognizer(
                        supported_entity=label,
                        supported_language=language,
                        patterns=patterns,
                        context=pattern_data["context"],
                    )
                )

    return registry


def _get_nlp_engine(languages: list[str]) -> NlpEngine:
    models = []

    for language in languages:
        if not spacy.util.is_package(f"{language}_core_web_sm"):
            # Use small spacy model, for faster inference.
            download(f"{language}_core_web_sm")
        models.append({"lang_code": language, "model_name": f"{language}_core_web_sm"})

    configuration = {"nlp_engine_name": "spacy", "models": models}

    return NlpEngineProvider(nlp_configuration=configuration).create_engine()


def get_transformers_recognizer(
    *,
    recognizer_conf: NERConfig,
    use_onnx: bool = False,
    supported_language: str = "en",
) -> EntityRecognizer:
    """
    This function loads a transformers recognizer given a recognizer configuration.

    Args:
        recognizer_conf: Configuration to recognize PII data.
        use_onnx: Whether to use the ONNX version of the model. Default is False.
        supported_language: The language to use for the recognizer. Default is "en".
    """
    model = recognizer_conf.get("DEFAULT_MODEL")
    supported_entities = recognizer_conf.get("PRESIDIO_SUPPORTED_ENTITIES")
    transformers_recognizer = TransformersRecognizer(
        model=model,
        supported_entities=supported_entities,
        supported_language=supported_language,
    )
    transformers_recognizer.load_transformer(
        use_onnx=use_onnx,
        **recognizer_conf,
    )
    return transformers_recognizer


def get_analyzer(
    recognizer: EntityRecognizer,
    regex_groups: list[RegexPattern],
    custom_names: list[str],
    supported_languages: list[str],
) -> AnalyzerEngine:
    nlp_engine = _get_nlp_engine(languages=supported_languages)

    registry = RecognizerRegistry(supported_languages=supported_languages)
    registry.load_predefined_recognizers(nlp_engine=nlp_engine)
    registry = _add_recognizers(registry, regex_groups, custom_names, supported_languages)
    registry.add_recognizer(recognizer)
    registry.remove_recognizer("SpacyRecognizer")

    return AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=supported_languages,
        context_aware_enhancer=LemmaContextAwareEnhancer(
            context_similarity_factor=0.35,
            min_score_with_context_similarity=0.4,
        ),
    )
