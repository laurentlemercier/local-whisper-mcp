import argparse
import asyncio
import base64
import json
import sys
import traceback
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def parse_args():
    parser = argparse.ArgumentParser(
        description="Client MCP Streamable HTTP pour local-whisper-service"
    )

    parser.add_argument(
        "--endpoint",
        required=True,
        help="Endpoint MCP, par exemple http://localhost:8000/mcp",
    )

    parser.add_argument(
        "--api-token",
        default=None,
        help="Token API/MCP",
    )

    parser.add_argument(
        "--audio-file",
        required=True,
        help="Fichier audio à transcrire",
    )

    parser.add_argument(
        "--model",
        default="small",
        choices=["small", "medium"],
        help="Modèle Whisper",
    )

    parser.add_argument(
        "--language",
        default="auto",
        help="Langue de transcription : auto, fr, en, etc.",
    )

    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Intervalle entre les interrogations du job",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Timeout HTTP en secondes",
    )

    return parser.parse_args()


def print_exception_details(exc: BaseException, indent: int = 0):
    """
    Affiche récursivement les ExceptionGroup afin de rendre les erreurs
    du SDK MCP beaucoup plus lisibles.
    """
    prefix = " " * indent

    if isinstance(exc, BaseExceptionGroup):
        print(
            f"{prefix}{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        for index, sub_exc in enumerate(exc.exceptions, start=1):
            print(
                f"{prefix}--- sub-exception {index} ---",
                file=sys.stderr,
            )
            print_exception_details(sub_exc, indent + 2)

    else:
        print(
            f"{prefix}{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def get_content_type(path: Path) -> str:
    suffix = path.suffix.lower()

    mapping = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }

    return mapping.get(suffix, "application/octet-stream")


def extract_tool_result(result):
    """
    Transforme le résultat MCP en dictionnaire Python.

    FastMCP peut retourner le résultat sous différentes formes selon
    la version du SDK.
    """

    # structuredContent est le cas idéal
    structured = getattr(result, "structuredContent", None)

    if structured is not None:
        return structured

    structured = getattr(result, "structured_content", None)

    if structured is not None:
        return structured

    # Fallback : essayer de récupérer le texte JSON
    content = getattr(result, "content", None)

    if content:
        for item in content:
            text = getattr(item, "text", None)

            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}

    return result


async def run_client(args):
    audio_path = Path(args.audio_file).expanduser().resolve()

    if not audio_path.exists():
        raise FileNotFoundError(
            f"Fichier audio introuvable : {audio_path}"
        )

    if not audio_path.is_file():
        raise RuntimeError(
            f"Le chemin n'est pas un fichier : {audio_path}"
        )

    audio_bytes = audio_path.read_bytes()
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    content_type = get_content_type(audio_path)

    print(f"MCP_ENDPOINT: {args.endpoint}")
    print(f"MCP_AUDIO_FILE: {audio_path}")
    print(f"MCP_AUDIO_BYTES: {len(audio_bytes)}")
    print(f"MCP_AUDIO_CONTENT_TYPE: {content_type}")

    headers = {}

    if args.api_token:
        headers["Authorization"] = f"Bearer {args.api_token}"

    timeout = httpx.Timeout(
        args.timeout,
        connect=min(args.timeout, 30.0),
    )

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
    ) as http_client:

        # IMPORTANT :
        # MCP SDK 1.30.0 :
        #
        # streamable_http_client() reçoit l'httpx.AsyncClient.
        # Les headers et timeout sont configurés sur AsyncClient,
        # pas directement sur streamable_http_client().
        #
        # Et le transport retourne 3 valeurs :
        #   read_stream
        #   write_stream
        #   get_session_id
        transport = streamable_http_client(
            args.endpoint,
            http_client=http_client,
        )

        async with transport as (
            read_stream,
            write_stream,
            get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
            ) as session:

                print("MCP_INITIALIZE: starting")

                await session.initialize()

                print("MCP_INITIALIZE: OK")

                # ---------------------------------------------------------
                # Découverte des outils
                # ---------------------------------------------------------

                tools_result = await session.list_tools()

                tool_names = [
                    tool.name
                    for tool in tools_result.tools
                ]

                print(
                    "MCP_TOOLS: "
                    + ", ".join(tool_names)
                )

                required_tools = {
                    "transcribe_data",
                    "get_transcription_status",
                    "get_transcription_result",
                }

                missing = required_tools - set(tool_names)

                if missing:
                    raise RuntimeError(
                        "Outils MCP manquants : "
                        + ", ".join(sorted(missing))
                    )

                print("MCP_DISCOVERY_OK: True")

                # ---------------------------------------------------------
                # Soumission
                # ---------------------------------------------------------

                print("MCP_SUBMIT: transcribe_data")
                print("MCP_CALL: transcribe_data")

                result = await session.call_tool(
                    "transcribe_data",
                    arguments={
                        # IMPORTANT :
                        # Le serveur attend data_base64.
                        "data_base64": encoded,
                        "filename": audio_path.name,
                        "content_type": content_type,
                        "model": args.model,
                        "language": args.language,
                        "debug": False,
                        "api_token": args.api_token,
                    },
                )

                if getattr(result, "isError", False):
                    raise RuntimeError(
                        "Tool MCP 'transcribe_data' a retourné une erreur : "
                        + str(result)
                    )

                submit_result = extract_tool_result(result)

                print(
                    "MCP_SUBMIT_RESULT: "
                    + json.dumps(
                        submit_result,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )

                if not isinstance(submit_result, dict):
                    raise RuntimeError(
                        "Réponse inattendue de transcribe_data : "
                        f"{type(submit_result).__name__}"
                    )

                job_id = submit_result.get("id")

                if not job_id:
                    raise RuntimeError(
                        "Aucun job_id dans la réponse MCP : "
                        + json.dumps(
                            submit_result,
                            ensure_ascii=False,
                            default=str,
                        )
                    )

                print(f"MCP_JOB_ID: {job_id}")

                # ---------------------------------------------------------
                # Polling
                # ---------------------------------------------------------

                loop = asyncio.get_running_loop()
                started = loop.time()

                while True:
                    elapsed = loop.time() - started

                    if elapsed > args.timeout:
                        raise TimeoutError(
                            f"Timeout en attente du job {job_id}"
                        )

                    await asyncio.sleep(args.poll_seconds)

                    status_result = await session.call_tool(
                        "get_transcription_status",
                        arguments={
                            "job_id": job_id,
                            "api_token": args.api_token,
                        },
                    )

                    if getattr(status_result, "isError", False):
                        raise RuntimeError(
                            "Tool MCP 'get_transcription_status' "
                            f"a retourné une erreur : {status_result}"
                        )

                    status = extract_tool_result(status_result)

                    print(
                        "MCP_STATUS: "
                        + json.dumps(
                            status,
                            ensure_ascii=False,
                            default=str,
                        )
                    )

                    if not isinstance(status, dict):
                        continue

                    state = status.get("status")

                    if state == "completed":
                        break

                    if state == "failed":
                        error = status.get("error", "Erreur inconnue")
                        raise RuntimeError(
                            f"Transcription échouée pour {job_id}: {error}"
                        )

                # ---------------------------------------------------------
                # Récupération du résultat final
                # ---------------------------------------------------------

                print("MCP_RESULT: get_transcription_result")

                final_result = await session.call_tool(
                    "get_transcription_result",
                    arguments={
                        "job_id": job_id,
                        "api_token": args.api_token,
                    },
                )

                if getattr(final_result, "isError", False):
                    raise RuntimeError(
                        "Tool MCP 'get_transcription_result' "
                        f"a retourné une erreur : {final_result}"
                    )

                result_data = extract_tool_result(final_result)

                print(
                    "MCP_FINAL_RESULT:"
                )

                print(
                    json.dumps(
                        result_data,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )

                # ---------------------------------------------------------
                # Résumé
                # ---------------------------------------------------------

                if isinstance(result_data, dict):
                    print()
                    print("MCP_TRANSCRIPTION_OK: True")

                    if "audio_duration_seconds" in result_data:
                        print(
                            "MCP_AUDIO_DURATION: "
                            f"{result_data['audio_duration_seconds']}"
                        )

                    if "transcription_duration_seconds" in result_data:
                        print(
                            "MCP_TRANSCRIPTION_DURATION: "
                            f"{result_data['transcription_duration_seconds']}"
                        )

                    if "real_time_factor" in result_data:
                        print(
                            "MCP_REAL_TIME_FACTOR: "
                            f"{result_data['real_time_factor']}"
                        )

                    result_object = result_data.get("result")

                    if isinstance(result_object, dict):
                        text = result_object.get("text")

                        if text:
                            print()
                            print("MCP_TEXT:")
                            print(text)

                return 0


def main():
    args = parse_args()

    try:
        return asyncio.run(run_client(args))

    except KeyboardInterrupt:
        print(
            "\nMCP_INTERRUPTED",
            file=sys.stderr,
        )
        return 130

    except Exception as exc:
        print(
            f"MCP_RUNTIME_ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        print(
            "MCP_EXCEPTION_DETAILS:",
            file=sys.stderr,
        )

        print_exception_details(exc)

        # Pour conserver la stack complète dans les logs
        traceback.print_exc()

        return 10


if __name__ == "__main__":
    sys.exit(main())