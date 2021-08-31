#!/usr/bin/env python3
"""Read DMARC aggregate reports drag-and-dropped from Thunderbird."""

import argparse
import logging
import mailbox
from io import BytesIO
import zipfile
import colorama
from colorama import Fore, Style  # , Back
from typing import Tuple
from enum import Flag

_LOGGER = logging.getLogger(__name__)


class MessageFlag(Flag):
    """Thunderbird message flag meanings.

    (incomplete)
    mailnews/base/public/nsMsgMessageFlags.idl
    X-Mozilla-Status header
    """

    READ = 1
    # ...
    EXPUNGED = 8
    # ...


class EmailUri():
    """Representation of a mailbox:// URI."""

    def __init__(self, uri):
        """Initialize the EmailUri object."""
        self.uri = uri

    @property
    def mbox_path(self):
        """Get path to the mbox file."""
        return self.uri.replace("mailbox://", "").split("?")[0]

    @property
    def message_number(self):
        """Get the message number."""
        return int(self.uri.split("?")[1].replace("number=", ""))


def message_from_mbox(mbox: mailbox.mbox, number: int) -> mailbox.mboxMessage:
    """Get message identified by number from Thunderbird mbox.

    number is Thunderbird's message number extracted from the mailbox:// URI.
    First message is number = 1.

    This function handles expunged messages. If message numbers still don't
    match, you probably just have a damaged .msf index. This can be easily
    fixed from Thunderbird's GUI (Repair Folder).
    It seems to happen every time a message is deleted from the folder.
    """
    # Seems like we need to filter out expunged messages for the
    # number to match.
    i = 0
    message_found = False
    for message in mbox:
        if int(message['X-Mozilla-Status']) & MessageFlag.EXPUNGED.value:
            continue
        i += 1  # We increment first, so that the first message is i = 1
        if i == number:
            # found the correct message
            message_found = True
            break
    if not message_found:
        raise ValueError(f"Message does not exist: {uri.uri}")
    return message


def xml_from_message(message: mailbox.mboxMessage) -> Tuple[str, str]:
    """Extract XML DMARC report from .zip attachment in e-mail message."""
    if message.is_multipart():
        _LOGGER.debug("Message is multipart")
        parts = message.get_payload()
        _LOGGER.debug(f"Message parts: {parts}")
        zip_parts = [part for part in parts
                     if part.get_content_type() == "application/zip"]
        if len(zip_parts) != 1:
            _LOGGER.error(f"Message parts with zip content type: {parts}")
            if len(zip_parts) == 0:
                raise ValueError("No zip attachments")
            else:
                raise ValueError("Too many zip attachments")
        message = zip_parts[0]
    content_type = message.get_content_type()
    if content_type != "application/zip":
        raise ValueError(f"Content-Type is not zip: {content_type}")
    # decode=True decodes base64
    # BytesIO acts as a fake in-memory file
    zip_file = zipfile.ZipFile(BytesIO(message.get_payload(decode=True)))
    if zip_file.testzip() is not None:
        _LOGGER.error(f"Zip CRC error: {zip_file.testzip()}")
        # TODO should we fail ??
    namelist = zip_file.namelist()
    if len(namelist) != 1:
        _LOGGER.info(f"Files in the zip: {namelist}")
        raise ValueError("Zip contains less or more than one file.")
    xml_filename = namelist[0]
    if not xml_filename.endswith(".xml"):
        raise ValueError("Zip does not contain a XML file.")
    _LOGGER.info(f"Unzipping file: {xml_filename}")
    xml_file = zip_file.read(xml_filename).decode(encoding="utf-8")
    return (xml_filename, xml_file)


def colorize_dmarc_xml(xml_file: str) -> str:
    """Colorize "pass" and "fail" in XML DMARC report."""
    return xml_file.replace(
                "fail", f"{Fore.RED}{Style.BRIGHT}fail{Style.RESET_ALL}"
            ).replace(
                "pass", f"{Fore.GREEN}{Style.BRIGHT}pass{Style.RESET_ALL}"
            ).replace(
                "<disposition>none</disposition>",
                f"<disposition>"
                f"{Fore.GREEN}{Style.BRIGHT}none{Style.RESET_ALL}"
                f"</disposition>"
            ).replace(
                "<disposition>quarantine</disposition>",
                f"<disposition>"
                f"{Fore.YELLOW}{Style.BRIGHT}quarantine{Style.RESET_ALL}"
                f"</disposition>"
            ).replace(
                "<disposition>reject</disposition>",  # TODO test
                f"<disposition>"
                f"{Fore.RED}{Style.BRIGHT}reject{Style.RESET_ALL}"
                f"</disposition>"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read DMARC aggregate reports drag-and-dropped "
                    "from Thunderbird.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="log INFO level messages")
    parser.add_argument("--debug", "-D", action="store_true",
                        help="log DEBUG level messages. Overrides --verbose.")
    parser.add_argument("--color", choices=["never", "always", "auto"],
                        default="auto",
                        help="colorize output. Default: %(default)s")
    parser.add_argument("--save", action="store_true",
                        help="save the xml file(s) in the current "
                             "working directory. Fail if file exists.")
    parser.add_argument("URI", nargs="+",
                        help="drag-and-dropped e-mail message URI")

    args = parser.parse_args()

    log_format = "%(levelname)s:%(message)s"
    if args.debug:
        logging.basicConfig(format=log_format, level=logging.DEBUG)
        _LOGGER.info("Logging DEBUG messages")
    elif args.verbose:
        logging.basicConfig(format=log_format, level=logging.INFO)
        _LOGGER.info("Logging INFO messages")
    else:
        logging.basicConfig(format=log_format, level=logging.WARNING)

    if args.color == "auto":
        # Colorama should automatically disable colors if stdout is not a tty
        colorama.init()
    elif args.color == "never":
        colorama.init(strip=True)
    elif args.color == "always":
        colorama.init(strip=False)

    for uri in args.URI:
        uri = EmailUri(uri)
        mbox_path = uri.mbox_path
        message_number = uri.message_number
        _LOGGER.info(f"Mailbox: {mbox_path}; message number: {message_number}")
        mbox = mailbox.mbox(mbox_path)
        message = message_from_mbox(mbox, message_number)
        _LOGGER.info(f"Subject: {message['subject']}")
        _LOGGER.debug(f"Keys: {message.keys()}")
        xml_filename, xml_file = xml_from_message(message)
        if args.save:
            _LOGGER.info(f"Saving as {xml_filename}")
            with open(xml_filename, "x") as f:
                f.write(xml_file)
        else:
            xml_file = colorize_dmarc_xml(xml_file)
            # Print the xml output to stdout, so that it can be piped to tools
            # like dmarc-cat -t xml -
            # If multiple URIs were specified, this will be quite a mess.
            print(xml_file)
