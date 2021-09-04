"""
Optimade support.
"""

import logging
import sys
from collections import namedtuple
from typing import Dict, Union, List, Optional
from urllib.parse import urlparse, urljoin

import requests

from pymatgen.core.periodic_table import DummySpecies
from pymatgen.core.structure import Structure
from pymatgen.util.sequence import PBar

# TODO: importing optimade-python-tool's data structures will make more sense
Provider = namedtuple("Provider", ["name", "base_url", "description", "homepage", "prefix"])

_logger = logging.getLogger(__name__)
_handler = logging.StreamHandler(sys.stdout)
_logger.addHandler(_handler)
_logger.setLevel(logging.WARNING)


class OptimadeRester:
    """
    Class to call OPTIMADE-compliant APIs, see optimade.org

    This class is ready to use but considered in-development and subject to change
    until the OPTIMADE paper is published.
    """

    # regenerate on-demand from official providers.json using OptimadeRester.refresh_aliases()
    # these aliases are provided as a convenient shortcut for users of the OptimadeRester class
    aliases = {
        "aflow": "http://aflow.org/API/optimade/",
        "cod": "https://www.crystallography.net/cod/optimade",
        "mcloud.2dstructures": "https://aiida.materialscloud.org/2dstructures/optimade",
        "mcloud.2dtopo": "https://aiida.materialscloud.org/2dtopo/optimade",
        "mcloud.curated-cofs": "https://aiida.materialscloud.org/curated-cofs/optimade",
        "mcloud.li-ion-conductors": "https://aiida.materialscloud.org/li-ion-conductors/optimade",
        "mcloud.optimade-sample": "https://aiida.materialscloud.org/optimade-sample/optimade",
        "mcloud.pyrene-mofs": "https://aiida.materialscloud.org/pyrene-mofs/optimade",
        "mcloud.scdm": "https://aiida.materialscloud.org/autowannier/optimade",
        "mcloud.sssp": "https://aiida.materialscloud.org/sssplibrary/optimade",
        "mcloud.stoceriaitf": "https://aiida.materialscloud.org/stoceriaitf/optimade",
        "mcloud.tc-applicability": "https://aiida.materialscloud.org/tc-applicability/optimade",
        "mcloud.threedd": "https://aiida.materialscloud.org/3dd/optimade",
        "mp": "https://optimade.materialsproject.org",
        "odbx": "https://optimade.odbx.science",
        "omdb.omdb_production": "http://optimade.openmaterialsdb.se",
        "oqmd": "http://oqmd.org/optimade/",
        "tcod": "https://www.crystallography.net/tcod/optimade",
    }

    def __init__(self, aliases_or_resource_urls: Optional[Union[str, List[str]]] = None, timeout=5):
        """
        OPTIMADE is an effort to provide a standardized interface to retrieve information
        from many different materials science databases.

        This is a client to retrieve structures from OPTIMADE v1 compliant endpoints. It
        does not yet support all features of the OPTIMADE v1 specification but is intended
        as a way to quickly search an endpoint in a way familiar to users of pymatgen without
        needing to know the full OPTIMADE specification.

        For advanced usage, please see the OPTIMADE documentation at optimate.org and
        consider calling the APIs directly.

        For convenience, known OPTIMADE endpoints have been given aliases in pymatgen to save
        typing the full URL. The current list of aliases is:

        aflow, cod, mcloud.sssp, mcloud.2dstructures, mcloud.2dtopo, mcloud.tc-applicability,
        mcloud.threedd, mcloud.scdm, mcloud.curated-cofs, mcloud.optimade-sample, mcloud.stoceriaitf,
        mcloud.pyrene-mofs, mcloud.li-ion-conductors, mp, odbx, omdb.omdb_production, oqmd, tcod

        To refresh this list of aliases, generated from the current list of OPTIMADE providers
        at optimade.org, call the refresh_aliases() method.

        This interface is maintained by @mkhorton, please contact him directly with bug reports
        or open an Issue in the pymatgen repository.

        Args:
            aliases_or_resource_urls: the alias or structure resource URL or a list of
            aliases or resource URLs, if providing the resource URL directly it should not
            be an index, this interface can only currently access the "v1/structures"
            information from the specified resource URL
            timeout: number of seconds before an attempted request is abandoned, a good
            timeout is useful when querying many providers, some of which may be offline
        """

        # TODO: maybe we should use the nice pydantic models from optimade-python-tools
        #  for response validation, and use the Lark parser for filter validation
        self.session = requests.Session()
        self._timeout = timeout  # seconds

        if isinstance(aliases_or_resource_urls, str):
            aliases_or_resource_urls = [aliases_or_resource_urls]

        # this stores a dictionary with keys provider id (in the same format as the aliases)
        # and values as the corresponding URL
        self.resources = {}

        if not aliases_or_resource_urls:
            aliases_or_resource_urls = list(self.aliases.keys())
            _logger.warning(
                "Connecting to all known OPTIMADE providers, this will be slow. Please connect to only the "
                f"OPTIMADE providers you want to query. Choose from: {', '.join(self.aliases.keys())}"
            )

        for alias_or_resource_url in aliases_or_resource_urls:

            if alias_or_resource_url in self.aliases:
                self.resources[alias_or_resource_url] = self.aliases[alias_or_resource_url]

            elif self._validate_provider(alias_or_resource_url):

                # TODO: unclear what the key should be here, the "prefix" is for the root provider,
                # may need to walk back to the index for the given provider to find the correct identifier

                self.resources[alias_or_resource_url] = alias_or_resource_url

            else:
                _logger.error(f"The following is not a known alias or a valid url: {alias_or_resource_url}")

        self._providers = {url: self._validate_provider(provider_url=url) for url in self.resources.values()}

    def __repr__(self):
        return f"OptimadeRester connected to: {', '.join(self.resources.values())}"

    def __str__(self):
        return self.describe()

    def describe(self):
        """
        Provides human-readable information about the resources being searched by the OptimadeRester.
        """
        provider_text = "\n".join(map(str, (provider for provider in self._providers.values() if provider)))
        description = f"OptimadeRester connected to:\n{provider_text}"
        return description

    @staticmethod
    def _build_filter(
        elements=None, nelements=None, nsites=None, chemical_formula_anonymous=None, chemical_formula_hill=None
    ):
        """
        Convenience method to build an OPTIMADE filter.
        """

        filters = []

        if elements:
            if isinstance(elements, str):
                elements = [elements]
            elements_str = ", ".join([f'"{el}"' for el in elements])
            filters.append(f"(elements HAS ALL {elements_str})")

        if nsites:
            if isinstance(nsites, (list, tuple)):
                filters.append(f"(nsites>={min(nsites)} AND nsites<={max(nsites)})")
            else:
                filters.append(f"(nsites={int(nsites)})")

        if nelements:
            if isinstance(nelements, (list, tuple)):
                filters.append(f"(nelements>={min(nelements)} AND nelements<={max(nelements)})")
            else:
                filters.append(f"(nelements={int(nelements)})")

        if chemical_formula_anonymous:
            filters.append(f'(chemical_formula_anonymous="{chemical_formula_anonymous}")')

        if chemical_formula_hill:
            filters.append(f'(chemical_formula_hill="{chemical_formula_anonymous}")')

        return " AND ".join(filters)

    def get_structures(
        self, elements=None, nelements=None, nsites=None, chemical_formula_anonymous=None, chemical_formula_hill=None,
    ) -> Dict[str, Structure]:
        """
        Retrieve structures from the OPTIMADE database.

        Not all functionality of OPTIMADE is currently exposed in this convenience method. To
        use a custom filter, call get_structures_with_filter().

        Args:
            elements: List of elements
            nelements: Number of elements, e.g. 4 or [2, 5] for the range >=2 and <=5
            nsites: Number of sites, e.g. 4 or [2, 5] for the range >=2 and <=5
            chemical_formula_anonymous: Anonymous chemical formula
            chemical_formula_hill: Chemical formula following Hill convention

        Returns: Dict of Structures keyed by that database's id system
        """

        optimade_filter = self._build_filter(
            elements=elements,
            nelements=nelements,
            nsites=nsites,
            chemical_formula_anonymous=chemical_formula_anonymous,
            chemical_formula_hill=chemical_formula_hill,
        )

        return self.get_structures_with_filter(optimade_filter)

    def get_structures_with_filter(self, optimade_filter: str) -> Dict[str, Structure]:
        """
        Get structures satisfying a given OPTIMADE filter.

        Args:
            filter: An OPTIMADE-compliant filter

        Returns: Dict of Structures keyed by that database's id system
        """

        all_structures = {}

        for identifier, resource in self.resources.items():

            fields = "response_fields=lattice_vectors,cartesian_site_positions,species,species_at_sites"

            url = urljoin(resource, f"v1/structures?filter={optimade_filter}&fields={fields}")

            try:

                json = self.session.get(url, timeout=self._timeout).json()

                structures = self._get_structures_from_resource(json, url)

                pbar = PBar(total=json["meta"].get("data_returned", 0), desc=identifier, initial=len(structures))

                # TODO: check spec for `more_data_available` boolean, may simplify this conditional
                if ("links" in json) and ("next" in json["links"]) and (json["links"]["next"]):
                    while "next" in json["links"] and json["links"]["next"]:
                        next_link = json["links"]["next"]
                        if isinstance(next_link, dict) and "href" in next_link:
                            next_link = next_link["href"]
                        json = self.session.get(next_link, timeout=self._timeout).json()
                        additional_structures = self._get_structures_from_resource(json, url)
                        structures.update(additional_structures)
                        pbar.update(len(additional_structures))

                if structures:

                    all_structures[identifier] = structures

            except Exception as exc:

                # TODO: manually inspect failures to either (a) correct a bug or (b) raise more appropriate error

                _logger.error(
                    f"Could not retrieve required information from provider {identifier} and url {url}: {exc}"
                )

        return all_structures

    @staticmethod
    def _get_structures_from_resource(json, url):

        structures = {}

        exceptions = set()

        def _sanitize_symbol(symbol):
            if symbol == "vacancy":
                symbol = DummySpecies("X_vacancy", oxidation_state=None)
            elif symbol == "X":
                symbol = DummySpecies("X", oxidation_state=None)
            return symbol

        def _get_comp(sp_dict):
            return {
                _sanitize_symbol(symbol): conc
                for symbol, conc in zip(sp_dict["chemical_symbols"], sp_dict["concentration"])
            }

        for data in json["data"]:

            # TODO: check the spec! and remove this try/except (are all providers following spec?)

            try:
                # e.g. COD
                structure = Structure(
                    lattice=data["attributes"]["lattice_vectors"],
                    species=[_get_comp(d) for d in data["attributes"]["species"]],
                    coords=data["attributes"]["cartesian_site_positions"],
                    coords_are_cartesian=True,
                )
                structures[data["id"]] = structure
            except Exception:

                try:
                    # e.g. MP (all ordered, no vacancies)
                    structure = Structure(
                        lattice=data["attributes"]["lattice_vectors"],
                        species=data["attributes"]["species_at_sites"],
                        coords=data["attributes"]["cartesian_site_positions"],
                        coords_are_cartesian=True,
                    )
                    structures[data["id"]] = structure

                except Exception as exc:
                    if str(exc) not in exceptions:
                        exceptions.add(str(exc))

        if exceptions:
            _logger.error(f'Failed to parse returned data for {url}: {", ".join(exceptions)}')

        return structures

    def _validate_provider(self, provider_url) -> Optional[Provider]:
        """
        Checks that a given URL is indeed an OPTIMADE provider,
        returning None if it is not a provider, or the provider
        prefix if it is.

        TODO: careful reading of OPTIMADE specification required
        TODO: add better exception handling, intentionally permissive currently
        """

        def is_url(url):
            """
            Basic URL validation thanks to https://stackoverflow.com/a/52455972
            """
            try:
                result = urlparse(url)
                return all([result.scheme, result.netloc])
            except ValueError:
                return False

        if not is_url(provider_url):
            _logger.warning(f"An invalid url was supplied: {provider_url}")
            return None

        try:
            provider_info_json = self.session.get(urljoin(provider_url, "v1/info"), timeout=self._timeout).json()
        except Exception as exc:
            _logger.warning(f"Failed to parse {urljoin(provider_url, 'v1/info')} when validating: {exc}")
            return None

        try:
            return Provider(
                name=provider_info_json["meta"].get("provider", {}).get("name", "Unknown"),
                base_url=provider_url,
                description=provider_info_json["meta"].get("provider", {}).get("description", "Unknown"),
                homepage=provider_info_json["meta"].get("provider", {}).get("homepage"),
                prefix=provider_info_json["meta"].get("provider", {}).get("prefix", "Unknown"),
            )
        except Exception as exc:
            _logger.warning(f"Failed to extract required information from {urljoin(provider_url, 'v1/info')}: {exc}")
            return None

    def _parse_provider(self, provider, provider_url) -> Dict[str, Provider]:
        """
        Used internally to update the list of providers or to
        check a given URL is valid.

        It does not raise exceptions but will instead _logger.warning and provide
        an empty dictionary in the case of invalid data.

        In future, when the specification  is sufficiently well adopted,
        we might be more strict here.

        Args:
            provider: the provider prefix
            provider_url: An OPTIMADE provider URL

        Returns:
            A dictionary of keys (in format of "provider.database") to
            Provider objects.
        """

        try:
            provider_link_json = self.session.get(urljoin(provider_url, "v1/links"), timeout=self._timeout).json()
        except Exception as exc:
            _logger.error(f"Failed to parse {urljoin(provider_url, 'v1/links')} when following links: {exc}")
            return {}

        def _parse_provider_link(provider, provider_link_json):
            """No validation attempted."""
            ps = {}
            try:
                d = [d for d in provider_link_json["data"] if d["attributes"]["link_type"] == "child"]
                for link in d:
                    key = f"{provider}.{link['id']}" if provider != link["id"] else provider
                    if link["attributes"]["base_url"]:
                        ps[key] = Provider(
                            name=link["attributes"]["name"],
                            base_url=link["attributes"]["base_url"],
                            description=link["attributes"]["description"],
                            homepage=link["attributes"].get("homepage"),
                            prefix=link["attributes"].get("prefix"),
                        )
            except Exception:
                # print(f"Failed to parse {provider}: {exc}")
                # Not all providers parse yet.
                pass
            return ps

        return _parse_provider_link(provider, provider_link_json)

    def refresh_aliases(self, providers_url="https://providers.optimade.org/providers.json"):
        """
        Updates available OPTIMADE structure resources based on the current list of OPTIMADE
        providers.
        """
        json = self.session.get(url=providers_url, timeout=self._timeout).json()
        providers_from_url = {
            entry["id"]: entry["attributes"]["base_url"] for entry in json["data"] if entry["attributes"]["base_url"]
        }

        structure_providers = {}
        for provider, provider_link in providers_from_url.items():
            structure_providers.update(self._parse_provider(provider, provider_link))

        self.aliases = {alias: provider.base_url for alias, provider in structure_providers.items()}

    # TODO: revisit context manager logic here and in MPRester
    def __enter__(self):
        """
        Support for "with" context.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Support for "with" context.
        """
        self.session.close()
