import json
import logging
import mwclient
import pickle
import re
import requests
import yaml

from mwtemplates import TemplateEditor

from xml.sax.saxutils import escape

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(level=logging.DEBUG)

CATEGORY_PREFIX = '[[Category:'


def _pickle(data, file):
    with open(file, 'wb') as handle:
        pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _unpickle(file):
    with open(file, 'rb') as f:
        return pickle.load(f)


def _data(file, fetch_from_api_func, fetch_from_api=False):
    if fetch_from_api:
        data = fetch_from_api_func(file)
    else:
        try:
            data = _unpickle(file)
        except FileNotFoundError:
            data = fetch_from_api_func(file)
    return data


def _add_text_to_page(page, text, replace_text=None):
    new_text = None
    debug_message = None
    debug_msg_template = '{page_name_text} now contains {text_text}'
    if page is not None:
        if page.exists:
            page_text = page.text()
            if replace_text is not None and \
                    replace_text in page_text:
                # If replace_text in page text replace it
                new_text = page_text.replace(replace_text, text)
                debug_message = debug_msg_template.format(
                    page_name_text=page.name,
                    text_text=new_text)
                debug_message += f', which replaced {replace_text}'
            elif CATEGORY_PREFIX not in page_text:
                # If no category in page text
                new_text = text
        else:
            new_text = text
        if new_text is not None:
            page.edit(new_text)
            if debug_message is None:
                debug_message = debug_msg_template.format(
                    page_name_text=page.name,
                    text_text=new_text)
            logger.debug(debug_message)


def _dict_from_api_endpoint(endpoint):
    return {
        datum['id']: datum['description']
        for datum in requests.get(endpoint).json()['data']
    }

class Organizations:

    # API
    API_BASE_URL = 'https://hr.apografi.gov.gr/api'
    ORGS_ENDPOINT = f'{API_BASE_URL}/public/organizations'
    SEARCH_ORG_ENDPOINT = f'{ORGS_ENDPOINT}/search'
    DICT_ENDPOINT = f'{API_BASE_URL}/public/metadata/dictionary'
    PURPOSES_DICT_ENDPOINT = f'{DICT_ENDPOINT}/Functions'
    TYPES_DICT_ENDPOINT = f'{DICT_ENDPOINT}/OrganizationTypes'
    STATUS_TRANSLATION = {
        'Active': 'Ενεργός',
        'Inactive': 'Ανενεργός'
    }
    # Site
    SITE_HOST = 'diadikasies.dev.grnet.gr'
    SITE_SCHEME = 'http'
    SITE_PATH = '/'
    SITE_CREDENTIALS_FILE = 'site_credentials.yaml'
    # Files
    HIERARCHY_PICKLE_FILE = 'org_hierarchy.pickle'
    DETAILS_PICKLE_FILE = 'org_details.pickle'
    # Mediawiki
    CATEGORY_NAME = 'Φορείς'
    CATEGORY = f'[[Category:{CATEGORY_NAME}]]'
    CATALOGUE_CATEGORY = '[[Category:Κατάλογος Φορέων]]'
    NAMESPACE = 'Φορέας'
    NAMESPACE_NUMBER = 9000
    TEMPLATE_FIELD_NAME_SUFFIXES = [
        'code',
        'preferredLabel',
        'alternativeLabels',
        'description',
        'url',
        'contactPoint_telephone',
        'contactPoint_email',
        'mainAddress_fullAddress',
        'mainAddress_postCode',
        'subOrganizationOf',
        'identifier',
        'purpose',
        'vatId',
        'status',
        'foundationDate',
        'terminationDate',
        'organizationType',
    ]
    TEMPLATE_NAME = 'Φορέας'
    TEMPLATE_PARAM_PREFIX = 'gov_org_'
    template_parameters = []
    for key in TEMPLATE_FIELD_NAME_SUFFIXES:
        template_parameters.append(f'|{TEMPLATE_PARAM_PREFIX}{key}=')
    TEMPLATE_PARAMETERS_TEXT = ''.join(template_parameters)
    TEMPLATE = f'{{{{{TEMPLATE_NAME}{TEMPLATE_PARAMETERS_TEXT}}}}}'

    def __init__(self):
        self._site = mwclient.Site(
            self.SITE_HOST, scheme=self.SITE_SCHEME, path=self.SITE_PATH)
        self.__site_credentials = None
        self._site_login()
        # API Dictionaries
        self.__name_by_code = {}
        self.__purpose_by_id = None
        self.__type_by_id = None

    @property
    def _site_credentials(self):
        if self.__site_credentials is None:
            with open(self.SITE_CREDENTIALS_FILE) as f:
                self.__site_credentials = tuple(yaml.safe_load(f).values())
        return self.__site_credentials

    @property
    def _purpose_by_id(self):
        if self.__purpose_by_id is None:
            self.__purpose_by_id = _dict_from_api_endpoint(
                self.PURPOSES_DICT_ENDPOINT)
        return self.__purpose_by_id

    @property
    def _type_by_id(self):
        if self.__type_by_id is None:
            self.__type_by_id = _dict_from_api_endpoint(
                self.TYPES_DICT_ENDPOINT)
        return self.__type_by_id

    def _site_login(self):
        logger.debug(self._site_credentials)
        self._site.login(*self._site_credentials)

    def _name_by_code(self, code):
        try:
            name = self.__name_by_code[code]
        except KeyError:
            name = requests.get(f'{self.ORGS_ENDPOINT}/{code}').json()['data'][
                'preferredLabel']
            self.__name_by_code[code] = name
        return name

    def _get_site_page(self, name, is_category=False):
        try:
            return self._site.categories[name] if is_category else \
                self._site.pages[name]
        except Exception as e:
            logger.error(e, name)
            return None

    def _fetch_hierarchy_from_api(self, file):
        logger.debug('Fetching org hierarchy from API...')
        orgs = requests.get(self.ORGS_ENDPOINT).json()['data']
        parent_children_orgs = {}
        for org in orgs:
            try:
                parent_code = org['subOrganizationOf']
            except KeyError:
                # parent_code does not exist, org contains a root body
                orgcode = org['code']
                if orgcode not in parent_children_orgs:
                    parent_children_orgs[orgcode] = {
                        org['preferredLabel']: []}
            else:
                try:
                    parentbody = parent_children_orgs[parent_code]
                except KeyError:
                    # Parent body does not exist
                    # Look in api orgs
                    for org2 in orgs:
                        if org2['code'] == parent_code:
                            # Found parent body, add child body
                            parent_children_orgs[parent_code] = {
                                org2['preferredLabel']: [org['preferredLabel']]
                            }
                            break
                else:
                    # Parent body already exists, append child body
                    parentbody[next(iter(parentbody))].append(
                        org['preferredLabel'])
                    parent_children_orgs[parent_code] = parentbody
        hierarchy = {}
        for parentcode, parent_children_dict in parent_children_orgs.items():
            hierarchy[next(iter(parent_children_dict))] = \
                list(parent_children_dict.values())[0]
        _pickle(hierarchy, file)
        return hierarchy

    def _fetch_details_from_api(self, file):
        logger.debug('Fetching org details from API...')
        details = {}
        for org in self._all_page_names(without_namespace=True):
            response = requests.post(self.SEARCH_ORG_ENDPOINT,
                                     data=json.dumps({"preferredLabel": org}))
            try:
                data = response.json()['data'][0]
            except IndexError:
                data = {}
            finally:
                details[org] = data
            # Replace parent code with parent name (preferredLabel)
            parent_code = details[org].get('subOrganizationOf')
            if parent_code:
                details[org]['subOrganizationOf'] = self._name_by_code(
                    parent_code)
            purpose_ids = details[org].get('purpose')
            # Replace purpose ids with purpose (function) names
            if purpose_ids:
                details[org]['purpose'] = ','.join([
                    self._purpose_by_id[id_] for id_ in purpose_ids])
            # Replace status with greek translation
            status = details[org].get('status')
            if status:
                details[org]['status'] = self.STATUS_TRANSLATION[status]
            # Replace type id with type name
            type_id = details[org].get('organizationType')
            if type_id:
                details[org]['organizationType'] = self._type_by_id[type_id]
            logger.debug(f'{org} - fetched details')
        _pickle(details, file)
        return details

    def _hierarchy(self, fetch_from_api=False):
        return _data(self.HIERARCHY_PICKLE_FILE,
                     self._fetch_hierarchy_from_api,
                     fetch_from_api=fetch_from_api)

    def _details(self, fetch_from_api=False):
        return _data(self.DETAILS_PICKLE_FILE,
                     self._fetch_details_from_api,
                     fetch_from_api=fetch_from_api)

    def _all_page_names(self, without_namespace=False):
        action = 'query'
        list_param = 'allpages'
        continue_param = 'continue'
        apcontinue_param = 'apcontinue'
        kwargs = {
            'format': 'json',
            'list': list_param,
            'apnamespace': self.NAMESPACE_NUMBER,
            'aplimit': 5000
        }
        page_names = []
        continue_value = 0
        while continue_value is not None:
            answer = self._site.api(action, **kwargs)
            continue_value = answer.get(
                continue_param, {}).get(apcontinue_param)
            kwargs[apcontinue_param] = continue_value
            page_names += [page_result['title']
                           for page_result in answer[action][list_param]]
        if without_namespace:
            page_names = [name.replace(f'{self.NAMESPACE}:', '')
                          for name in page_names]
        return page_names

    def _all_pages(self):
        for name in self._all_page_names():
            page = self._get_site_page(name)
            if page is not None:
                yield page

    def _create_pages(self, name, parent_category=None):
        if parent_category is None:
            parent_category = self.CATEGORY
            replace_text = None
        else:
            replace_text = self.CATEGORY
        category_page = self._get_site_page(name, is_category=True)
        _add_text_to_page(category_page, parent_category,
                          replace_text=replace_text)
        page = self._get_site_page(f'{self.NAMESPACE}:{name}')
        _add_text_to_page(page, self.CATALOGUE_CATEGORY)

    def recreate_tree(self):
        logger.debug('Creating organization category tree and pages...')
        for parent, children in self._hierarchy().items():
            self._create_pages(parent)
            parent_category = f'[[Category:{parent}]]'
            for child in children:
                self._create_pages(
                    child, parent_category=parent_category)
        logger.debug('Done.')

    def _nuke_tree(self):
        logger.debug('Nuking organization category tree and pages...')

        def recurse_delete(page):
            if page.exists:
                page_is_category = True
                try:
                    page_members = page.members()
                except AttributeError:
                    # page is not a category (no members)
                    page_is_category = False
                else:
                    # page is a category
                    for member in page_members:
                        recurse_delete(member)
                finally:
                    if page_is_category or page.name.startswith(
                            self.NAMESPACE):
                        page.delete()
                        logger.debug(f'{page.name} deleted.')
        root_category_page = self._site.categories[self.CATEGORY_NAME]
        for page in root_category_page.members():
            recurse_delete(page)
        logger.debug('Done.')

    def update_pages(self):
        logger.debug('Updating organization pages...')

        def template_text(org_details):
            te = TemplateEditor(self.TEMPLATE)
            template = te.templates[self.TEMPLATE_NAME][0]
            # Add details to template parameters
            for key in self.TEMPLATE_FIELD_NAME_SUFFIXES:
                if '_' in key:
                    details_keys = key.split('_')
                else:
                    details_keys = None
                if details_keys is None:
                    value = org_details.get(key, None)
                else:
                    value = org_details.get(details_keys[0], {}).get(
                        details_keys[1], None)
                if value is not None:
                    if isinstance(value, list):
                        value = ','.join(value)
                    clean_value = escape(str(value))
                    template.parameters[
                        f'{self.TEMPLATE_PARAM_PREFIX}{key}'] = clean_value
            return str(template).replace(' |', '|')
        for org, org_details in self._details().items():
            page = self._get_site_page(f'{self.NAMESPACE}:{org}')
            if page is not None and page.exists:
                page_text = page.text()
                page_text_leftovers = re.sub(
                    rf'{{{{{self.TEMPLATE_NAME}[^{{}}]+}}}}', '',
                    page_text).strip()
                new_template_text = template_text(org_details)
                new_page_text = f'{new_template_text}\n{page_text_leftovers}'
                page.edit(new_page_text)
                logger.debug(f'{page.name} updated')
        logger.debug('Done.')