import joblib
import os
import numpy as np
import logging
import json

# Configure logging
logger = logging.getLogger(__name__)

# ---------------- PATH SETUP ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_PATH = os.path.join(
    BASE_DIR,
    "models",
    "crop_model_py312.pkl"
)

FEATURE_NAMES = [
    "Nitrogen", "Phosphorus", "Potassium",
    "Temperature", "Humidity", "pH", "Rainfall"
]

VALID_CROPS = ["wheat", "maize", "rice", "chickpea", "pigeonpeas", "cotton"]

# Agronomy-inspired constraints used as soft penalties, not hard rules.
FEASIBILITY_RULES = {
    "wheat": {
        "temp": (10, 28),
        "rainfall": (200, 900),
        "humidity": (35, 85),
        "ph": (6.0, 7.8),
    },
    "maize": {
        "temp": (18, 34),
        "rainfall": (300, 1200),
        "humidity": (45, 90),
        "ph": (5.5, 7.8),
    },
    "rice": {
        "temp": (20, 38),
        "rainfall": (700, 1500),
        "humidity": (60, 100),
        "ph": (5.0, 7.5),
    },
    "chickpea": {
        "temp": (10, 30),
        "rainfall": (200, 900),
        "humidity": (30, 75),
        "ph": (6.0, 8.2),
    },
    "pigeonpeas": {
        "temp": (18, 35),
        "rainfall": (400, 1300),
        "humidity": (40, 90),
        "ph": (5.0, 8.0),
    },
    "cotton": {
        "temp": (20, 38),
        "rainfall": (300, 1000),
        "humidity": (35, 85),
        "ph": (5.5, 8.0),
    },
}

# ---------------- LAZY MODEL LOADING ----------------
# Singleton model instance - loaded only when needed
_model = None


def _load_model():
    """
    Lazily load the model with proper error handling.
    This function loads the model only when first called.
    
    Returns:
        model: The loaded sklearn model
        
    Raises:
        FileNotFoundError: If model file doesn't exist
        Exception: If model fails to load
    """
    global _model
    
    if _model is not None:
        return _model
    
    # Check if model file exists first
    if not os.path.exists(MODEL_PATH):
        logger.error(f"Model file not found at: {MODEL_PATH}")
        raise FileNotFoundError(f"Model not found at {MODEL_PATH}")
    
    try:
        logger.info(f"Loading crop recommendation model from: {MODEL_PATH}")
        _model = joblib.load(MODEL_PATH)
        logger.info("Crop recommendation model loaded successfully")
        return _model
    except Exception as e:
        logger.error(f"Failed to load crop recommendation model: {str(e)}")
        raise


def get_model():
    """
    Public function to get the model instance.
    Use this to access the model safely.
    
    Returns:
        model: The loaded sklearn model or None if failed
    """
    try:
        return _load_model()
    except Exception as e:
        logger.error(f"Error getting model: {str(e)}")
        return None


def is_model_available():
    """
    Check if model is available and loaded.
    
    Returns:
        bool: True if model is available, False otherwise
    """
    global _model
    if _model is not None:
        return True
    
    # Try to load and check
    try:
        _load_model()
        return True
    except Exception:
        return False


def rule_based_engine(N, P, K, temp, humidity, ph, rainfall):
    """
    Apply deterministic agronomic rules before ML fallback.
    """
    if N < 50 and temp <= 25 and rainfall <= 700:
        return "chickpea", 0.15

    if 15 <= temp <= 25 and rainfall < 600:
        return "wheat", 0.12

    if N > 60 and 20 <= temp <= 30:
        return "maize", 0.10

    if rainfall > 800 and humidity > 70:
        return "rice", 0.13

    if 25 <= temp <= 35 and rainfall < 800:
        return "cotton", 0.08

    return None, None


def adjust_probabilities(probs, classes, features):
    """
    Lightweight post-processing boost for high-signal scenarios.
    """
    N, P, K, temp, humidity, ph, rainfall = features

    adjusted = []
    for crop, prob in zip(classes, probs):
        score = float(prob)
        crop_name = str(crop).lower()

        if crop_name == "chickpea" and N < 50:
            score += 0.10

        if crop_name == "rice" and rainfall > 800:
            score += 0.10

        adjusted.append(score)

    return np.array(adjusted, dtype=float)


def _range_feasibility(value, low, high):
    if low <= value <= high:
        return 1.0

    span = max(high - low, 1e-6)
    distance = low - value if value < low else value - high

    if distance <= 0.15 * span:
        return 0.85
    if distance <= 0.30 * span:
        return 0.65
    return 0.35


def feasibility_multiplier(crop, features):
    crop_key = str(crop).lower()
    rules = FEASIBILITY_RULES.get(crop_key)
    if not rules:
        return 1.0

    N, P, K, temp, humidity, ph, rainfall = features
    factors = [
        _range_feasibility(temp, rules["temp"][0], rules["temp"][1]),
        _range_feasibility(rainfall, rules["rainfall"][0], rules["rainfall"][1]),
        _range_feasibility(humidity, rules["humidity"][0], rules["humidity"][1]),
        _range_feasibility(ph, rules["ph"][0], rules["ph"][1]),
    ]

    return float(np.prod(factors))


def validate_with_groq(top_crops, features):
    """
    Re-rank uncertain predictions with Groq. Falls back silently on failure.
    """
    try:
        from groq import Groq

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return top_crops

        client = Groq(api_key=api_key)

        prompt = f"""
You are an agriculture expert.

Input conditions:
N={features[0]}, P={features[1]}, K={features[2]},
Temp={features[3]}, Humidity={features[4]},
pH={features[5]}, Rainfall={features[6]}

Model predicted crops (JSON):
{json.dumps(top_crops, ensure_ascii=True)}

Task:
1. Re-rank crops using realistic agronomic suitability
2. Adjust confidence values to realistic percentages
3. Remove unrealistic crops
4. Keep only crops from this set: wheat, maize, rice, chickpea, pigeonpeas, cotton

Return only valid JSON list like:
[
  {{"crop": "rice", "confidence": 68.5}},
  {{"crop": "maize", "confidence": 14.2}}
]
"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )

        raw_content = (response.choices[0].message.content or "").strip()

        start = raw_content.find("[")
        end = raw_content.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return top_crops

        parsed = json.loads(raw_content[start:end + 1])
        if not isinstance(parsed, list):
            return top_crops

        validated = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            crop = str(item.get("crop", "")).strip().lower()
            conf = item.get("confidence")
            if crop not in VALID_CROPS:
                continue
            try:
                conf = max(0.0, min(100.0, float(conf)))
            except (TypeError, ValueError):
                continue
            validated.append({"crop": crop, "confidence": conf})

        if not validated:
            return top_crops

        total = sum(item["confidence"] for item in validated)
        if total <= 0:
            return top_crops

        return [
            {
                "crop": item["crop"],
                "confidence": round((item["confidence"] / total) * 100, 2)
            }
            for item in validated
        ]
    except Exception as e:
        logger.warning(f"Groq validation failed; using local ranking: {str(e)}")
        return top_crops


def dynamic_suggestion_count(top_confidence):
    if top_confidence >= 75:
        return 3
    if top_confidence >= 60:
        return 4
    return 5


def _is_uncertain_prediction(ranked):
    if not ranked:
        return False
    top_conf = ranked[0]["confidence"]
    second_conf = ranked[1]["confidence"] if len(ranked) > 1 else 0.0
    return top_conf < 60.0 or (top_conf - second_conf) < 12.0


# ---------------- PREDICTION ----------------
def predict_crop(features):
    """
    Predict the best crop based on input features.
    Uses lazy loading - model is loaded only when this function is called.
    
    features = [N, P, K, temperature, humidity, ph, rainfall]

    Returns:
      best_crop (str)
      confidence (float)
      suitability (dict: high / medium / low)
      feature_importance (list of dict)
      
    Raises:
      ValueError: If features are invalid
      RuntimeError: If model fails to load
    """
    # -------- Validation --------
    if not isinstance(features, (list, tuple)):
        raise ValueError("features must be a list or tuple of numeric values")
    
    if len(features) != 7:
        raise ValueError("features must be a list of 7 numeric values")
    
    # Validate all values are numeric
    try:
        features = [float(f) for f in features]
    except (TypeError, ValueError) as e:
        raise ValueError(f"All features must be numeric: {str(e)}")

    N, P, K, temp, humidity, ph, rainfall = features

    # -------- Load model lazily --------
    try:
        model = _load_model()
    except Exception as e:
        logger.error(f"Cannot predict - model loading failed: {str(e)}")
        raise RuntimeError(f"ML model is not available: {str(e)}")

    if model is None:
        logger.error("Model is None after loading")
        raise RuntimeError("ML model failed to load")

    X = np.array(features, dtype=float).reshape(1, -1)

    # -------- Model prediction --------
    try:
        probs = model.predict_proba(X)[0]
        classes = model.classes_
    except Exception as e:
        logger.error(f"Prediction failed: {str(e)}")
        raise RuntimeError(f"Prediction failed: {str(e)}")

    filtered = [
        (c, p) for c, p in zip(classes, probs)
        if str(c).lower() in VALID_CROPS
    ]

    if filtered:
        classes, probs = zip(*filtered)
        probs = np.array(probs, dtype=float)
        classes = list(classes)
    else:
        classes = list(classes)
        probs = np.array(probs, dtype=float)
        logger.warning("No valid crops remained after filtering; using original model outputs")

    probs = adjust_probabilities(probs, classes, features)

    rule_crop, rule_boost = rule_based_engine(N, P, K, temp, humidity, ph, rainfall)
    if rule_crop:
        for idx, crop in enumerate(classes):
            if str(crop).lower() == rule_crop:
                probs[idx] += rule_boost
                break

    feasibility = np.array(
        [feasibility_multiplier(crop, features) for crop in classes],
        dtype=float,
    )
    probs = probs * feasibility

    total = float(np.sum(probs))
    if total > 0:
        probs = probs / total
    else:
        raise RuntimeError("Unable to compute valid probabilities after constraints")

    top_idx = np.argsort(probs)[::-1]
    if len(top_idx) == 0:
        raise RuntimeError("No prediction candidates available")

    ranked = [
        {
            "crop": str(classes[i]).lower(),
            "confidence": round(float(probs[i]) * 100, 2),
        }
        for i in top_idx
    ]

    if _is_uncertain_prediction(ranked):
        ranked = validate_with_groq(ranked[:5], features)

    if not ranked:
        raise RuntimeError("No ranked crop predictions available")

    ranked.sort(key=lambda x: float(x["confidence"]), reverse=True)

    top_conf = float(ranked[0]["confidence"])
    suggestion_count = min(dynamic_suggestion_count(top_conf), len(ranked))

    suggestions = ranked[:suggestion_count]
    full_ranking = ranked

    best_crop = str(suggestions[0]["crop"]).capitalize()
    best_conf = round(float(suggestions[0]["confidence"]), 2)

    suitability = {
        "high": [(best_crop, best_conf)],
        "medium": [],
        "low": []
    }

    for item in suggestions[1:]:
        suitability["medium"].append(
            (str(item["crop"]).capitalize(), round(float(item["confidence"]), 2))
        )

    for item in full_ranking[suggestion_count:]:
        suitability["low"].append(
            (str(item["crop"]).capitalize(), round(float(item["confidence"]), 2))
        )

    logger.info(f"Prediction successful: {best_crop} with {best_conf}% confidence")

    return best_crop, best_conf, suitability, full_ranking
