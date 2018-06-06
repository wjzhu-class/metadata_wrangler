from nose.tools import set_trace
from sqlalchemy.orm.session import Session

from core.coverage import CollectionCoverageProvider
from core.model import (
    Collection,
    ConfigurationSetting,
    DataSource,
    ExternalIntegration,
    Identifier,
)
from core.metadata_layer import ReplacementPolicy
from core.overdrive import (
    OverdriveBibliographicCoverageProvider as BaseOverdriveBibliographicCoverageProvider,
    OverdriveAPI
)
from core.mirror import MirrorUploader

from coverage_utils import ResolveVIAFOnSuccessCoverageProvider

class OverdriveBibliographicCoverageProvider(
        ResolveVIAFOnSuccessCoverageProvider,
        BaseOverdriveBibliographicCoverageProvider
):
    """Finds and updates bibliographic information for Overdrive items."""

    EXCLUDE_SEARCH_INDEX = True

    def __init__(self, collection, viaf=None, replacement_policy=None,
                 **kwargs):
        _db = Session.object_session(collection)
        api_class = kwargs.pop('api_class', OverdriveAPI)
        if callable(api_class):
            api = self.generic_overdrive_api(_db, api_class)
        else:
            # The API 'class' is actually an object, probably a mock.
            api = api_class
        if not api:
            raise CannotLoadConfiguration(
                """OverdriveBibliographicCoverageProvider requires at least one fully configured Overdrive collection."""
            )

        self.viaf = viaf or VIAFClient(_db)

        if not replacement_policy:
            mirror = MirrorUploader.sitewide(_db)
            replacement_policy = ReplacementPolicy.from_metadata_source(
                mirror=self.mirror
            )

        kwargs['registered_only'] = True
        super(OverdriveBibliographicCoverageProvider, self).__init__(
            collection, api_class=api, replacement_policy=replacement_policy,
            **kwargs
        )

    @classmethod
    def generic_overdrive_api(cls, _db, api_class):
        """Create an OverdriveAPI that will work for metadata
        wrangler purposes.

        As the metadata wrangler, most of our Overdrive
        'collections' aren't actually configured with Overdrive
        credentials. We can't create an OverdriveAPI specially for
        each Overdrive collection.

        But all we need is _one_ properly configured Overdrive
        Collection. Overdrive allows us to get bibliographic
        information about any book on Overdrive, not just ones
        associated with a specific collection.

        If we have one such Collection, we can create an
        OverdriveAPI that can be used in every collection.
        """
        qu = _db.query(Collection).join(
            Collection._external_integration
        ).join(
            ExternalIntegration.settings
        ).filter(
            ExternalIntegration.protocol==ExternalIntegration.OVERDRIVE
        ).filter(
            ExternalIntegration.goal==ExternalIntegration.LICENSE_GOAL
        ).filter(ConfigurationSetting.key==ExternalIntegration.USERNAME)

        configured_collections = qu.all()
        if not configured_collections:
            return None
        configured_collection = configured_collections[0]
        return api_class(_db, configured_collection)

    def items_that_need_coverage(self, identifiers=None, **kwargs):
        """Finds the items that need coverage based on the collection's catalog
        instead of its license_pools. This is specific to work done on the
        Metadata Wrangler.

        TODO: Find a better way to combine Overdrive bibliographic coverage
        with catalog coverage. This approach represents a duplication of
        work in core.coverage.CatalogCoverageProvider.items_that_need_coverage.
        """
        qu = super(CollectionCoverageProvider, self).items_that_need_coverage(
            identifiers, **kwargs
        )
        qu = qu.join(Identifier.collections).filter(
            Collection.id==self.collection_id
        )
        return qu

    def handle_success(self, identifier):
        result = super(
            OverdriveBibliographicCoverageProvider, self
        ).handle_success(identifier)
        return self.handle_success_mixin(result)
