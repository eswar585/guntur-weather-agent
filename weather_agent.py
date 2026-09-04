"""Send a morning weather report for Guntur to a Telegram chat."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GUNTUR = {
    "name": "Guntur",
    "region": "Andhra Pradesh, India",
    "latitude": 16.3067,
    "longitude": 80.4365,
}
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TELEGRAM_API_URL = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 15


class WeatherAgentError(RuntimeError):
    """Raised when weather data cannot be fetched or a report cannot be sent."""


@dataclass(frozen=True)
class WeatherReport:
    temperature_c: float
    condition: str
    humidity_percent: int
    wind_speed_kmh: float
    rain_probability_percent: int
    observed_at: str


WEATHER_CONDITIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = Request(url, headers=headers or {"User-Agent": "guntur-weather-agent/1.0"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise WeatherAgentError(
            f"Weather service returned HTTP {error.code}."
        ) from error
    except (URLError, TimeoutError) as error:
        raise WeatherAgentError("Could not reach the weather or Telegram service.") from error
    except json.JSONDecodeError as error:
        raise WeatherAgentError("The service returned an invalid JSON response.") from error

    if not isinstance(payload, dict):
        raise WeatherAgentError("The service returned an unexpected response.")
    return payload


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)):
        raise WeatherAgentError(f"Weather response is missing {key}.")
    return float(value)


def _current_hour_index(current_time: str, hourly_times: list[Any]) -> int:
    for index, hour in enumerate(hourly_times):
        if hour == current_time:
            return index

    # Open-Meteo normally returns an exact matching local-hour timestamp.
    # This fallback handles a minor timestamp format difference gracefully.
    try:
        current_hour = datetime.fromisoformat(current_time).replace(minute=0, second=0)
        for index, hour in enumerate(hourly_times):
            if datetime.fromisoformat(str(hour)).replace(minute=0, second=0) == current_hour:
                return index
    except (TypeError, ValueError):
        pass

    raise WeatherAgentError("Could not match the current time to the rain forecast.")


def fetch_weather() -> WeatherReport:
    """Fetch Guntur's current conditions and current-hour rain probability."""
    query = urlencode(
        {
            "latitude": GUNTUR["latitude"],
            "longitude": GUNTUR["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "hourly": "precipitation_probability",
            "forecast_days": 1,
            "timezone": "Asia/Kolkata",
        }
    )
    payload = _get_json(f"{OPEN_METEO_URL}?{query}")

    current = payload.get("current")
    hourly = payload.get("hourly")
    if not isinstance(current, dict) or not isinstance(hourly, dict):
        raise WeatherAgentError("Weather response is missing current or hourly data.")

    weather_code = int(_number(current, "weather_code"))
    hourly_times = hourly.get("time")
    rain_probabilities = hourly.get("precipitation_probability")
    current_time = current.get("time")
    if not isinstance(hourly_times, list) or not isinstance(rain_probabilities, list):
        raise WeatherAgentError("Weather response is missing the rain forecast.")
    if not isinstance(current_time, str):
        raise WeatherAgentError("Weather response is missing the observation time.")

    index = _current_hour_index(current_time, hourly_times)
    if index >= len(rain_probabilities):
        raise WeatherAgentError("Rain forecast data is incomplete.")

    rain_probability = rain_probabilities[index]
    if not isinstance(rain_probability, (int, float)):
        raise WeatherAgentError("Rain probability is not available.")

    return WeatherReport(
        temperature_c=_number(current, "temperature_2m"),
        condition=WEATHER_CONDITIONS.get(weather_code, "Unknown conditions"),
        humidity_percent=round(_number(current, "relative_humidity_2m")),
        wind_speed_kmh=_number(current, "wind_speed_10m"),
        rain_probability_percent=round(float(rain_probability)),
        observed_at=current_time,
    )


def format_morning_report(report: WeatherReport) -> str:
    """Format weather details as an attractive Telegram morning report."""
    try:
        observed_at = datetime.fromisoformat(report.observed_at).strftime("%I:%M %p").lstrip("0")
    except ValueError:
        observed_at = report.observed_at

    return (
        "🌅 *GOOD MORNING, ESWAR!* ☀️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📍 *Guntur, Andhra Pradesh*\n\n"
        "🌤️ *TODAY'S WEATHER*\n"
        f"☁️ Condition: *{report.condition}*\n"
        f"🌡️ Temperature: *{report.temperature_c:.1f}°C*\n"
        f"💧 Humidity: *{report.humidity_percent}%*\n"
        f"💨 Wind: *{report.wind_speed_kmh:.1f} km/h*\n"
        f"🌧️ Rain Chance: *{report.rain_probability_percent}%*\n\n"
        f"🕐 Updated: {observed_at}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✨ *Have a wonderful day!* ✨"
    )


def send_to_telegram(message: str, bot_token: str, chat_id: str) -> None:
    """Send a report through the Telegram Bot API."""
    url = f"{TELEGRAM_API_URL}/bot{bot_token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "guntur-weather-agent/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise WeatherAgentError(f"Telegram returned HTTP {error.code}.") from error
    except (URLError, TimeoutError) as error:
        raise WeatherAgentError("Could not reach Telegram.") from error
    except json.JSONDecodeError as error:
        raise WeatherAgentError("Telegram returned an invalid response.") from error

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = payload.get("description", "Telegram rejected the message.") if isinstance(payload, dict) else "Telegram returned an unexpected response."
        raise WeatherAgentError(description)


def main() -> int:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", bot_token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        print(
            f"Missing required environment variable(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    try:
        report = fetch_weather()
        message = format_morning_report(report)
        send_to_telegram(message, bot_token, chat_id)
    except WeatherAgentError as error:
        print(f"Weather agent failed: {error}", file=sys.stderr)
        return 1

    print("Morning weather report sent to Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
