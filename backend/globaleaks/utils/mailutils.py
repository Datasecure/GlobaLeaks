# -*- coding: utf-8
#
# mailutils
# *********
#
# GlobaLeaks Utility used to handle Mail, format, exception, etc

import StringIO
import re
import sys
import traceback
from email import utils, Charset  # pylint: disable=no-name-in-module
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from txsocksx.client import SOCKS5ClientEndpoint

from twisted.internet import reactor, defer
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.mail.smtp import ESMTPSenderFactory, SMTPError
from twisted.protocols import tls
from twisted.python.failure import Failure

from globaleaks import __version__
from globaleaks.state import State
from globaleaks.security import encrypt_message, sha256
from globaleaks.settings import Settings
from globaleaks.utils.tls import TLSClientContextFactory
from globaleaks.utils.utility import log


def MIME_mail_build(src_name, src_mail, dest_name, dest_mail, title, mail_body):
    # Override python's weird assumption that utf-8 text should be encoded with
    # base64, and instead use quoted-printable (for both subject and body).  I
    # can't figure out a way to specify QP (quoted-printable) instead of base64 in
    # a way that doesn't modify global state. :-(
    Charset.add_charset('utf-8', Charset.QP, Charset.QP, 'utf-8')

    # This example is of an email with text and html alternatives.
    multipart = MIMEMultipart('alternative')

    # We need to use Header objects here instead of just assigning the strings in
    # order to get our headers properly encoded (with QP).
    # You may want to avoid this if your headers are already ASCII, just so people
    # can read the raw message without getting a headache.
    multipart['Subject'] = Header(title.encode('utf-8'), 'UTF-8').encode()
    multipart['Date'] = utils.formatdate()
    multipart['To'] = Header(dest_name.encode('utf-8'), 'UTF-8').encode() + " <" + dest_mail + ">"
    multipart['From'] = Header(src_name.encode('utf-8'), 'UTF-8').encode() + " <" + src_mail + ">"
    multipart['X-Mailer'] = "fnord"

    multipart.attach(MIMEText(mail_body.encode('utf-8'), 'plain', 'UTF-8'))

    return StringIO.StringIO(multipart.as_string())


def sendmail(to_address, subject, body):
    """
    Sends an email using SMTPS/SMTP+TLS and torify the connection

    @param to_address: the to address field of the email
    @param subject: the mail subject
    @param body: the mail body
    @param event: the event description, needed to keep track of failure/success
    """
    try:
        if not to_address:
            return

        result_deferred = defer.Deferred()

        def errback(reason, *args, **kwargs):
            # TODO: here it should be written a complete debugging of the possible
            #       errors by writing clear log lines in relation to all the stack:
            #       e.g. it should debugged all errors related to: TCP/SOCKS/TLS/SSL/SMTP/SFIGA
            if isinstance(reason, Failure):
                log.err("SMTP connection failed (Exception: %s)", reason.value)
                log.debug(reason)

            return result_deferred.errback(reason)

        result_deferred.addErrback(errback)

        authentication_username=State.tenant_cache[1].notif.username
        authentication_password=State.tenant_cache[1].private.smtp_password
        from_address=State.tenant_cache[1].notif.source_email
        smtp_host=State.tenant_cache[1].notif.server
        smtp_port=State.tenant_cache[1].notif.port
        security=State.tenant_cache[1].notif.security

        message = MIME_mail_build(State.tenant_cache[1].notif.source_name,
                                  State.tenant_cache[1].notif.source_email,
                                  to_address,
                                  to_address,
                                  subject,
                                  body)

        log.debug('Sending email to %s using SMTP server [%s:%d] [%s]',
                  to_address,
                  smtp_host,
                  smtp_port,
                  security)

        context_factory = TLSClientContextFactory()

        factory = ESMTPSenderFactory(
            authentication_username.encode('utf-8'),
            authentication_password.encode('utf-8'),
            from_address,
            to_address,
            message,
            result_deferred,
            contextFactory=context_factory,
            requireAuthentication=True,
            requireTransportSecurity=(security != 'SSL'),
            retries=0,
            timeout=Settings.mail_timeout)

        if security == "SSL":
            factory = tls.TLSMemoryBIOFactory(context_factory, True, factory)

        if State.tenant_cache[1].anonymize_outgoing_connections:
            socksProxy = TCP4ClientEndpoint(reactor, Settings.socks_host, Settings.socks_port, timeout=Settings.mail_timeout)
            endpoint = SOCKS5ClientEndpoint(smtp_host.encode('utf-8'), smtp_port, socksProxy)
        else:
            endpoint = TCP4ClientEndpoint(reactor, smtp_host.encode('utf-8'), smtp_port, timeout=Settings.mail_timeout)

        if Settings.testing:
            #  Hooking the test down to here is a trick to be able to test all the above code :)
            return defer.succeed(None)

        d = endpoint.connect(factory)
        d.addErrback(errback)

        return result_deferred

    except Exception as excep:
        # we strongly need to avoid raising exception inside email logic to avoid chained errors
        log.err("Unexpected exception in sendmail: %s", str(excep))
        return defer.fail()


def schedule_exception_email(exception_text, *args):
    try:
        from globaleaks.transactions import schedule_email

        if not hasattr(State.tenant_cache[1], 'notif'):
            log.err("Error: Cannot send mail exception before complete initialization.")
            return

        if State.exceptions_email_count >= Settings.exceptions_email_hourly_limit:
            return

        exception_text = (exception_text % args) if args else exception_text

        sha256_hash = sha256(bytes(exception_text))

        if sha256_hash not in State.exceptions:
            State.exceptions[sha256_hash] = 0

        State.exceptions[sha256_hash] += 1
        if State.exceptions[sha256_hash] > 5:
            log.err("Exception mail suppressed for (%s) [reason: threshold exceeded]",  sha256_hash)
            return

        State.exceptions_email_count += 1

        mail_subject = "GlobaLeaks Exception"
        delivery_list = State.tenant_cache[1].notif.exception_delivery_list

        if Settings.devel_mode:
            mail_subject +=  " [%s]" % Settings.developer_name
            delivery_list = [("globaleaks-stackexception-devel@globaleaks.org", '')]

        exception_text = bytes("Platform: %s (%s)\nVersion: %s\n\n%s" \
                               % (State.tenant_cache[1].hostname,
                                  State.tenant_cache[1].onionservice,
                                  __version__,
                                  exception_text))

        for mail_address, pub_key in delivery_list:
            mail_body = exception_text

            # Opportunisticly encrypt the mail body. NOTE that mails will go out
            # unencrypted if one address in the list does not have a public key set.
            if pub_key:
                mail_body = encrypt_message(pub_key, mail_body)

            # avoid waiting for the notification to send and instead rely on threads to handle it
            schedule_email(mail_address, mail_subject, mail_body)

    except Exception as excep:
        # Avoid raising exception inside email logic to avoid chaining errors
        log.err("Unexpected exception in send_exception_mail: %s", excep)


def mail_exception_handler(etype, value, tback):
    """
    Formats traceback and exception data and emails the error,
    This would be enabled only in the testing phase and testing release,
    not in production release.
    """
    if Settings.disable_backend_exception_notification:
        return

    if isinstance(value, (GeneratorExit,
                          defer.AlreadyCalledError,
                          SMTPError)) or \
        (etype == AssertionError and value.message == "Request closed"):
        # we need to bypass email notification for some exception that:
        # 1) raise frequently or lie in a twisted bug;
        # 2) lack of useful stacktraces;
        # 3) can be cause of email storm amplification
        #
        # this kind of exception can be simply logged error logs.
        log.err("exception mail suppressed for exception (%s) [reason: special exception]", str(etype))
        return

    # collection of the stacktrace info
    exc_type = re.sub("(<(type|class ')|'exceptions.|'>|__main__.)",
                      "", str(etype))
    error_message = "%s %s" % (exc_type.strip(), etype.__doc__)
    traceinfo = '\n'.join(traceback.format_exception(etype, value, tback))

    mail_body = error_message + "\n\n" + traceinfo

    log.err("Unhandled exception raised:")
    log.err(mail_body)

    schedule_exception_email(mail_body)


def extract_exception_traceback_and_schedule_email(e):
    if isinstance(e, Failure):
        mail_exception_handler(e.type, e.value, e.getTracebackObject())
    else:
        mail_exception_handler(*sys.exc_info())
