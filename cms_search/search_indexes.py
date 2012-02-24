import re

from django.conf import settings
#from django.core.exceptions import ImproperlyConfigured
from django.contrib.sites.models import Site
from django.db.models import Q
from django.db.models.query import EmptyQuerySet
from django.template import RequestContext
from django.test.client import RequestFactory
from django.utils.encoding import force_unicode
from django.utils.translation import get_language, activate


def _strip_tags(value):
    """
    Returns the given HTML with all tags stripped.

    This is a copy of django.utils.html.strip_tags, except that it adds some
    whitespace in between replaced tags to make sure words are not erroneously
    concatenated.
    """
    return re.sub(r'<[^>]*?>', ' ', force_unicode(value))

try:
    import importlib
except ImportError:
    from django.utils import importlib

from haystack import indexes, connections

from cms.models.pluginmodel import CMSPlugin

from cms_search import models as proxy_models
from cms_search import settings as search_settings

rf = RequestFactory()

def page_index_factory(language_code, proxy_model):

    class _PageIndex(indexes.SearchIndex, indexes.Indexable):
#        _language = language_code
        language = indexes.CharField()

        text = indexes.CharField(document=True, use_template=False)
        pub_date = indexes.DateTimeField(model_attr='publication_date', null=True)
        login_required = indexes.BooleanField(model_attr='login_required')
        url = indexes.CharField(stored=True, indexed=False, model_attr='get_absolute_url')
        title = indexes.CharField(stored=True, indexed=False, model_attr='get_title')
        site_id = indexes.IntegerField(stored=True, indexed=True, model_attr='site_id')

        def prepare(self, obj):
            current_languge = get_language()
            try:
                if current_languge != language_code:
                    activate(language_code)
                request = rf.get("/")
                request.session = {}
                request.LANGUAGE_CODE = language_code
                self.prepared_data = super(_PageIndex, self).prepare(obj)
                plugins = CMSPlugin.objects.filter(language=language_code, placeholder__in=obj.placeholders.all())
                text = u''
                for base_plugin in plugins:
                    instance, plugin_type = base_plugin.get_plugin_instance()
                    if instance is None:
                        # this is an empty plugin
                        continue
                    if hasattr(instance, 'search_fields'):
                        text += u' '.join(force_unicode(_strip_tags(getattr(instance, field, ''))) for field in instance.search_fields)
                    if getattr(instance, 'search_fulltext', False) or getattr(plugin_type, 'search_fulltext', False):
                        text += _strip_tags(instance.render_plugin(context=RequestContext(request))) + u' '
                text += obj.get_meta_description() or u''
                text += u' '
                text += obj.get_meta_keywords() or u''
                self.prepared_data['text'] = text
                self.prepared_data['language'] = language_code
                return self.prepared_data
            finally:
                if get_language() != current_languge:
                    activate(current_languge)

        def get_model(self):
            return proxy_model

        def index_queryset(self):
            # get the correct language and exclude pages that have a redirect
            base_qs = super(_PageIndex, self).index_queryset()
            result_qs = EmptyQuerySet()
            for site_obj in Site.objects.all():
                qs = base_qs.published(site=site_obj.id).filter(
                    Q(title_set__language=language_code) & (Q(title_set__redirect__exact='') | Q(title_set__redirect__isnull=True)))
                if 'publisher' in settings.INSTALLED_APPS:
                    qs = qs.filter(publisher_is_draft=True)
                qs = qs.distinct()
                result_qs |= qs
            return result_qs

    return _PageIndex

# we don't want the globals() style which was used in models.py ...
def push_indices():
    magic_indices = []
    for language_code, language_name in settings.LANGUAGES:
        proxy_model = getattr(proxy_models, proxy_models.proxy_name(language_code))
        magic_indices.append( page_index_factory(language_code, proxy_model))

    unified_index = connections['default'].get_unified_index()
    prev_indices = [index for key, index in unified_index.indexes.iteritems()]
    all_indices = [ind() for ind in magic_indices] + prev_indices
    unified_index.build(indexes=all_indices)
push_indices()
