"""Pinned CPU embedding models and independent index formats."""

DEFAULT = "multilingual-e5-small"
MODELS = {
    DEFAULT: {
        "label": "Multilingual E5 Small · 487 MB",
        "repo": "intfloat/multilingual-e5-small",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "onnx": "onnx/model.onnx",
        "model_hash": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
        "vocabulary_sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    },
    "e5-small-v2-int8": {
        "label": "English E5 Small · 35 MB",
        "repo": "Xenova/e5-small-v2",
        "revision": "02af79985278377e65c724a76275707cb0333c70",
        "onnx": "onnx/model_quantized.onnx",
        "model_hash": "7d9092cb25f2bd1c023b7e8d2aa459044a02030ac880e5a59fdaf27af69f1ded",
        "vocabulary_sha256": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    },
}


def folder(base, name):
    if name not in MODELS:
        raise ValueError("Unknown embedding model")
    return base if name == DEFAULT else base / name
