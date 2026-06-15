
import sys
import os

# Add current directory to path so we can import charlie
sys.path.append(os.getcwd())

from charlie.voice import VoiceEngine
from charlie.config import config

def test_preprocessing():
    # We don't need a real callback or to start the engine threads
    engine = VoiceEngine(config, lambda x: None)
    
    test_cases = [
        ("$965B", "nine hundred sixty five billion dollars"),
        ("$1.5M", "one point five million dollars"),
        ("$100", "one hundred dollars"),
        ("$1", "one dollar"),
        ("valuation of 965B", "valuation of nine hundred sixty five billion"),
        ("about 1.5M users", "about one point five million users"),
        ("1,000,000 dollars", "one million dollars"),
        ("12345", "twelve thousand three hundred forty five"),
        ("price is $2,000.50", "price is two thousand point five zero dollars"),
    ]
    
    passed = 0
    for input_text, expected in test_cases:
        # _sanitize_for_tts calls _numbers_to_words
        result = engine._numbers_to_words(input_text)
        if result.lower() == expected.lower():
            print(f"PASS: '{input_text}' -> '{result}'")
            passed += 1
        else:
            print(f"FAIL: '{input_text}' -> '{result}' (Expected: '{expected}')")
            
    print(f"\nResult: {passed}/{len(test_cases)} tests passed.")

if __name__ == "__main__":
    test_preprocessing()
