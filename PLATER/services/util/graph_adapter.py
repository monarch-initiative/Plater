import base64
import traceback

import aiohttp
import requests

from PLATER.services.config import config
from PLATER.services.util.logutil import LoggingUtil

logger = LoggingUtil.init_logging(__name__,
                                  config.get('logging_level'),
                                  config.get('logging_format')
                                  )


class Neo4jHTTPDriver:
    def __init__(self, host: str, port: int,  auth: set, scheme: str = 'http'):
        self._host = host
        self._neo4j_transaction_endpoint = "/db/data/transaction/commit"
        self._scheme = scheme
        self._full_transaction_path = f"{self._scheme}://{self._host}:{port}{self._neo4j_transaction_endpoint}"
        self._port = port
        self._supports_apoc = None
        self._header = {
                'Accept': 'application/json; charset=UTF-8',
                'Content-Type': 'application/json',
                'Authorization': 'Basic %s' % base64.b64encode(f"{auth[0]}:{auth[1]}".encode('utf-8')).decode('utf-8')
            }
        # ping and raise error if neo4j doesn't respond.
        logger.debug('PINGING NEO4J')
        self.ping()
        logger.debug('CHECKING IF NEO4J SUPPORTS APOC')
        self.check_apoc_support()
        logger.debug(f'SUPPORTS APOC : {self._supports_apoc}')

    async def post_request_json(self, payload):
        tcp_connector = aiohttp.TCPConnector(limit=60)
        async with aiohttp.ClientSession(connector=tcp_connector) as session:
            async with session.post(self._full_transaction_path, json=payload, headers=self._header) as response:
                if response.status != 200:
                    logger.error(f"[x] Problem contacting Neo4j server {self._host}:{self._port} -- {response.status}")
                    txt = await response.text()
                    logger.debug(f"[x] Server responded with {txt}")
                else:
                    return await response.json()

    def ping(self):
        """
        Pings the neo4j backend.
        :return:
        """
        neo4j_db_labels_endpoint = "/db/data/labels"
        ping_url = f"{self._scheme}://{self._host}:{self._port}{neo4j_db_labels_endpoint}"
        # if we can't contact neo4j, we should exit.
        try:
            import time
            now = time.time()
            response = requests.get(ping_url, headers=self._header)
            later = time.time()
            time_taken = later - now
            logger.debug(f'Contacting neo4j took {time_taken} seconds.')
            if time_taken > 5:  # greater than 5 seconds it's not healthy
                logger.warn(f"Contacting neo4j took more than 5 seconds ({time_taken}). Neo4j might be stressed.")
            if response.status_code != 200:
                raise Exception(f'server returned {response.status_code}')
        except Exception as e:
            logger.error(f"Error contacting Neo4j @ {ping_url} -- Exception raised -- {e}")
            logger.debug(traceback.print_exc())
            raise RuntimeError('Connection to Neo4j could not be established.')

    async def run(self, query):
        """
        Runs a neo4j query async.
        :param query: Cypher query.
        :type query: str
        :return: result of query.
        :rtype: dict
        """
        # make the statement dictionary
        payload = {
            "statements": [
                {
                    "statement": f"{query}"
                }
            ]
        }

        response = await self.post_request_json(payload)
        errors = response.get('errors')
        if errors:
            logger.error(f'Neo4j returned `{errors}` for cypher {query}.')
            raise RuntimeWarning(f'Error running cypher {query}.')
        return response

    def run_sync(self, query):
        """
        Runs a neo4j query. Can cause the async loop to block.
        :param query:
        :return:
        """
        payload = {
            "statements": [
                {
                    "statement": f"{query}"
                }
            ]
        }
        response = requests.post(
            self._full_transaction_path,
            headers=self._header,
            json=payload).json()
        errors = response.get('errors')
        if errors:
            logger.error(f'Neo4j returned `{errors}` for cypher {query}.')
            raise RuntimeWarning(f'Error running cypher {query}.')
        return response

    def convert_to_dict(self, response: dict) -> list:
        """
        Converts a neo4j result to a structured result.
        :param response: neo4j http raw result.
        :type response: dict
        :return: reformatted dict
        :rtype: dict
        """
        results = response.get('results')
        array = []
        if results:
            for result in results:
                cols = result.get('columns')
                if cols:
                    data_items = result.get('data')
                    for item in data_items:
                        new_row = {}
                        row = item.get('row')
                        for col_name, col_value in zip(cols, row):
                            new_row[col_name] = col_value
                        array.append(new_row)
        return array

    def check_apoc_support(self):
        apoc_version_query = 'call apoc.help("meta")'
        if self._supports_apoc is None:
            try:
                self.run_sync(apoc_version_query)
                self._supports_apoc = True
            except:
                self._supports_apoc = False
        return self._supports_apoc


class GraphInterface:
    """
    Singleton class for interfacing with the graph.
    """

    class _GraphInterface:
        def __init__(self, host, port, auth):
            self.driver = Neo4jHTTPDriver(host=host, port=port, auth=auth)
            self.schema = None
            self.summary = None

        def get_schema(self):
            """
            Gets the schema of the graph. To be used by. Also generates graph summary
            :return: Dict of structure source label as outer most keys, target labels as inner keys and list of predicates
            as value.
            :rtype: dict
            """
            self.schema_raw_result = {}
            if self.schema is None:
                query = """
                           MATCH (a)-[x]->(b)
                           WHERE not a:Concept and not b:Concept                                                          
                           RETURN DISTINCT labels(a) as source_labels, type(x) as predicate, labels(b) as target_labels
                           """
                logger.info(f"starting query {query} on graph... this might take a few")
                result = self.driver.run_sync(query)
                logger.info(f"completed query, preparing initial schema")
                structured = self.convert_to_dict(result)
                self.schema_raw_result = structured
                schema_bag = {}
                # permituate source labels and target labels array
                # replacement for unwind for previous cypher
                structured_expanded = []
                for triplet in structured:
                    source_labels, predicate, target_labels = triplet['source_labels'], \
                                                              triplet['predicate'], \
                                                              triplet['target_labels']
                    for source_label in source_labels:
                        for target_label in target_labels:
                            structured_expanded.append(
                                {
                                    'source_label': source_label,
                                    'target_label': target_label,
                                    'predicate': predicate
                                }
                            )
                structured = structured_expanded
                for triplet in structured:
                    subject = triplet['source_label']
                    predicate = triplet['predicate']
                    objct = triplet['target_label']
                    if subject not in schema_bag:
                        schema_bag[subject] = {}
                    if objct not in schema_bag[subject]:
                        schema_bag[subject][objct] = []
                    if predicate not in schema_bag[subject][objct]:
                        schema_bag[subject][objct].append(predicate)
                    # do reverse
                    if objct not in schema_bag:
                        schema_bag[objct] = {}
                    if subject not in schema_bag[objct]:
                        schema_bag[objct][subject] = []
                    if predicate not in schema_bag[objct][subject]:
                        schema_bag[objct][subject].append(predicate)
                self.schema = schema_bag
                logger.info("schema done.")
                if not self.summary:
                    query = """
                    MATCH (c) RETURN DISTINCT labels(c) as types, count(c) as count                
                    """
                    logger.info(f'generating graph summary: {query}')
                    raw = self.convert_to_dict(self.driver.run_sync(query))
                    summary = {}
                    for node in raw:
                        labels = node['types']
                        count = node['count']
                        query = f"""
                        MATCH (:{':'.join(labels)})-[e]->(b) WITH DISTINCT e , b 
                        RETURN 
                            type(e) as edge_types, 
                            count(e) as edge_counts,
                            labels(b) as target_labels 
                        """
                        raw = self.convert_to_dict(self.driver.run_sync(query))
                        summary_key = ':'.join(labels)
                        summary[summary_key] = {
                            'nodes_count': count
                        }
                        for row in raw:
                            target_key = ':'.join(row['target_labels'])
                            edge_name = row['edge_types']
                            edge_count = row['edge_counts']
                            summary[summary_key][target_key] = summary[summary_key].get(target_key, {})
                            summary[summary_key][target_key][edge_name] = edge_count
                    self.summary = summary
                    logger.info(f'generated summary for {len(summary)} node types.')
            return self.schema

        async def get_mini_schema(self, source_id, target_id):
            """
            Given either id of source and/or target returns predicates that relate them. And their
            possible labels.
            :param source_id:
            :param target_id:
            :return:
            """
            source_id_syntaxed = f"{{id: \"{source_id}\"}}" if source_id else ''
            target_id_syntaxed = f"{{id: \"{target_id}\"}}" if target_id else ''
            query = f"""
                            MATCH (a{source_id_syntaxed})-[x]->(b{target_id_syntaxed}) WITH
                                [la in labels(a) where la <> 'Concept'] as source_label,
                                [lb in labels(b) where lb <> 'Concept'] as target_label,
                                type(x) as predicate
                            RETURN DISTINCT source_label, predicate, target_label
                        """
            response = await self.driver.run(query)
            response = self.convert_to_dict(response)
            return response

        async def get_node(self, node_type: str, curie: str) -> list:
            """
            Returns a node that matches curie as its ID.
            :param node_type: Type of the node.
            :type node_type:str
            :param curie: Curie.
            :type curie: str
            :return: value of the node in neo4j.
            :rtype: list
            """
            query = f"MATCH (c:`{node_type}`{{id: '{curie}'}}) return c"
            response = await self.driver.run(query)

            data = response.get('results',[{}])[0].get('data', [])
            '''
            data looks like 
            [
            {'row': [{...node data..}], 'meta': [{...}]},
            {'row': [{...node data..}], 'meta': [{...}]},
            {'row': [{...node data..}], 'meta': [{...}]}
            ]            
            '''
            rows = []
            if len(data):
                from functools import reduce
                rows = reduce(lambda x, y: x + y.get('row', []), data, [])
            return rows

        async def get_single_hops(self, source_type: str, target_type: str, curie: str) -> list:
            """
            Returns a triplets of source to target where source id is curie.
            :param source_type: Type of the source node.
            :type source_type: str
            :param target_type: Type of target node.
            :type target_type: str
            :param curie: Curie of source node.
            :type curie: str
            :return: list of triplets where each item contains source node, edge, target.
            :rtype: list
            """

            query = f'MATCH (c:`{source_type}`{{id: \'{curie}\'}})-[e]->(b:`{target_type}`) return distinct c , e, b'
            response = await self.driver.run(query)
            rows = list(map(lambda data: data['row'], response['results'][0]['data']))
            query = f'MATCH (c:`{source_type}`{{id: \'{curie}\'}})<-[e]-(b:`{target_type}`) return distinct b , e, c'
            response = await self.driver.run(query)
            rows += list(map(lambda data: data['row'], response['results'][0]['data']))

            return rows

        async def run_cypher(self, cypher: str) -> list:
            """
            Runs cypher directly.
            :param cypher: cypher query.
            :type cypher: str
            :return: unprocessed neo4j response.
            :rtype: list
            """
            return await self.driver.run(cypher)

        async def get_sample(self, node_type):
            """
            Returns a few nodes.
            :param node_type: Type of nodes.
            :type node_type: str
            :return: Node dict values.
            :rtype: dict
            """
            query = f"MATCH (c:{node_type}) return c limit 5"
            response = await self.driver.run(query)
            rows = response['results'][0]['data'][0]['row']
            return rows

        async def get_examples(self, source, target=None):
            """
            Returns an example for source node only if target is not specified, if target is specified a sample one hop
            is returned.
            :param source: Node type of the source node.
            :type source: str
            :param target: Node type of the target node.
            :type target: str
            :return: A single source node value if target is not provided. If target is provided too, a triplet.
            :rtype:
            """
            if target:
                query = f"MATCH (source:{source})-[edge]->(target:{target}) return source, edge, target limit 1"
                response = await self.run_cypher(query)
                final = list(map(lambda data: data['row'], response['results'][0]['data']))
                return final
            else:
                query = f"MATCH ({source}:{source}) return {source} limit 1"
                response = await self.run_cypher(query)
                final = list(map(lambda node: node[source], self.driver.convert_to_dict(response)))
                return final

        def supports_apoc(self):
            """
            Returns true if apoc is supported by backend database.
            :return: bool true if neo4j supports apoc.
            """
            return self.driver.check_apoc_support()

        async def run_apoc_cover(self, ids: list):
            """
            Runs apoc.algo.cover on list of ids
            :param ids:
            :return: dictionary of edges and source and target nodes ids
            """
            query = f"""
                        MATCH (node:named_thing)
                        USING INDEX node:named_thing(id)
                        WHERE node.id in {ids}
                        WITH collect(node) as nodes
                        CALL apoc.algo.cover(nodes) yield rel
                        WITH {{source_id: startNode(rel).id ,
                               target_id: endNode(rel).id,
                               edge: rel }} as row
                        return collect(row) as result                                        
                        """
            result = self.convert_to_dict(self.driver.run_sync(query))
            return result

        def convert_to_dict(self, result):
            return self.driver.convert_to_dict(result)

    instance = None

    def __init__(self, host, port, auth):
        # create a new instance if not already created.
        if not GraphInterface.instance:
            GraphInterface.instance = GraphInterface._GraphInterface(host=host, port=port, auth=auth)

    def __getattr__(self, item):
        # proxy function calls to the inner object.
        return getattr(self.instance, item)
