# -*- coding: utf-8
#
#   /admin/contexts
#   *****
# Implementation of the code executed on handler /admin/contexts
#
from globaleaks import models
from globaleaks.handlers.admin.modelimgs import db_get_model_img
from globaleaks.handlers.base import BaseHandler
from globaleaks.orm import transact
from globaleaks.rest import requests
from globaleaks.state import State
from globaleaks.utils.structures import fill_localized_keys, get_localized_values
from globaleaks.utils.utility import log


def admin_serialize_context(store, context, language):
    """
    Serialize the specified context

    :param store: the store on which perform queries.
    :param language: the language in which to localize data.
    :return: a dictionary representing the serialization of the context.
    """
    receivers = [id for id in store.find(models.ReceiverContext.receiver_id, models.ReceiverContext.context_id == context.id)]
    picture = db_get_model_img(store, models.Context, context.id)

    ret_dict = {
        'id': context.id,
        'tip_timetolive': context.tip_timetolive,
        'select_all_receivers': context.select_all_receivers,
        'maximum_selectable_receivers': context.maximum_selectable_receivers,
        'show_context': context.show_context,
        'show_recipients_details': context.show_recipients_details,
        'allow_recipients_selection': context.allow_recipients_selection,
        'show_small_receiver_cards': context.show_small_receiver_cards,
        'enable_comments': context.enable_comments,
        'enable_messages': context.enable_messages,
        'enable_two_way_comments': context.enable_two_way_comments,
        'enable_two_way_messages': context.enable_two_way_messages,
        'enable_attachments': context.enable_attachments,
        'enable_rc_to_wb_files': context.enable_rc_to_wb_files,
        'presentation_order': context.presentation_order,
        'show_receivers_in_alphabetical_order': context.show_receivers_in_alphabetical_order,
        'questionnaire_id': context.questionnaire_id,
        'receivers': receivers,
        'picture': picture
    }

    return get_localized_values(ret_dict, context, context.localized_keys, language)


@transact
def get_context_list(store, language):
    """
    Returns the context list.

    :param store: the store on which perform queries.
    :param language: the language in which to localize data.
    :return: a dictionary representing the serialization of the contexts.
    """
    return [admin_serialize_context(store, context, language)
        for context in store.find(models.Context)]


def db_associate_context_receivers(store, context, receivers_ids):
    store.find(models.ReceiverContext, models.ReceiverContext.context_id == context.id).remove()

    for receiver_id in receivers_ids:
        store.add(models.ReceiverContext({'context_id': context.id,
                                          'receiver_id': receiver_id}))


@transact
def get_context(store, context_id, language):
    """
    Returns:
        (dict) the context with the specified id.
    """
    context = models.db_get(store, models.Context, id=context_id)

    return admin_serialize_context(store, context, language)


def fill_context_request(request, language):
    fill_localized_keys(request, models.Context.localized_keys, language)

    request['tip_timetolive'] = -1 if request['tip_timetolive'] < 0 else request['tip_timetolive']

    if request['select_all_receivers']:
        if request['maximum_selectable_receivers']:
            log.debug("Resetting maximum_selectable_receivers (%d) because 'select_all_receivers' is True",
                      request['maximum_selectable_receivers'])
        request['maximum_selectable_receivers'] = 0

    return request


def db_update_context(store, context, request, language):
    request = fill_context_request(request, language)

    if not request['questionnaire_id']:
        request['questionnaire_id'] = State.tenant_cache[1].default_questionnaire

    context.update(request)

    db_associate_context_receivers(store, context, request['receivers'])

    return context


def db_create_context(store, request, language):
    request = fill_context_request(request, language)

    if not request['questionnaire_id']:
        request['questionnaire_id'] = u'default'

    if not request['allow_recipients_selection']:
        request['select_all_receivers'] = True

    context = models.db_forge_obj(store, models.Context, request)

    db_associate_context_receivers(store, context, request['receivers'])

    return context


@transact
def create_context(store, request, language):
    """
    Creates a new context from the request of a client.

    Args:
        (dict) the request containing the keys to set on the model.

    Returns:
        (dict) representing the configured context
    """
    context = db_create_context(store, request, language)

    return admin_serialize_context(store, context, language)


@transact
def update_context(store, context_id, request, language):
    """
    Updates the specified context. If the key receivers is specified we remove
    the current receivers of the Context and reset set it to the new specified
    ones.

    Args:
        context_id:

        request:
            (dict) the request to use to set the attributes of the Context

    Returns:
            (dict) the serialized object updated
    """
    if not request['allow_recipients_selection']:
        request['select_all_receivers'] = True

    context = models.db_get(store, models.Context, id=context_id)
    context = db_update_context(store, context, request, language)

    return admin_serialize_context(store, context, language)


class ContextsCollection(BaseHandler):
    check_roles = 'admin'
    cache_resource = True
    invalidate_cache = True

    def get(self):
        """
        Return all the contexts.

        Parameters: None
        Response: adminContextList
        """
        return get_context_list(self.request.language)

    def post(self):
        """
        Create a new context.

        Request: AdminContextDesc
        Response: AdminContextDesc
        """
        request = self.validate_message(self.request.content.read(),
                                        requests.AdminContextDesc)

        return create_context(request, self.request.language)


class ContextInstance(BaseHandler):
    check_roles = 'admin'
    invalidate_cache = True

    def put(self, context_id):
        """
        Update the specified context.

        Parameters: context_id
        Request: AdminContextDesc
        Response: AdminContextDesc
        """
        request = self.validate_message(self.request.content.read(),
                                        requests.AdminContextDesc)

        return update_context(context_id, request, self.request.language)

    def delete(self, context_id):
        """
        Delete the specified context.

        Request: AdminContextDesc
        Response: None
        """
        return models.delete(models.Context, id=context_id)
