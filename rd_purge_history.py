#! /usr/bin/env python
import argparse
import logging
import requests
import sys
import time
import xml.etree.ElementTree as etree
from distutils.util import strtobool


def get_projects(client):
    request = 'projects'
    res = client.get(request)
    res.raise_for_status()
    t = etree.fromstring(res.text)
    return [x.find("name").text for x in t]


def search_history(client, project, job_filter=None, offset=0, hmax=0):
    request = 'history?project={p}&offset={o}&max={m}'.format(
        p=project,
        o=offset,
        m=hmax,
    )

    if job_filter:
        request += '&jobFilter=' + job_filter

    res = client.get(request)
    res.raise_for_status()
    return etree.fromstring(res.text)


def get_execution_ids(client, project, job_filter, offset, hmax):
    root = search_history(client, project, job_filter, offset, hmax)
    return [event.find('./execution').get('id') for event in root]


def get_history_total(client, project, job_filter):
    history = search_history(client, project, job_filter)
    return int(history.get('total'))


def purge_history(
        client,
        project,
        job_filter,
        keep_history_size,
        chunk_size,
        max_delete_size,
        dry_run
):
    total = get_history_total(client, project, job_filter)
    keep_history_size = min(total, keep_history_size)
    deletions = min(max_delete_size, total - keep_history_size)
    logging.info(
        "{}/{} histories are going to be deleted..".format(deletions, total))

    chunk_num, remains = divmod(deletions, chunk_size)

    offset = total
    deleted = 0
    for i in range(chunk_num):
        '''
        Delete by chunk
        '''
        offset = total - chunk_size * (i + 1)
        ids = get_execution_ids(
            client, project, job_filter, offset, chunk_size)
        ids = ids[-chunk_size:]

        deleted += client.delete_executions(ids, dry_run)

        time.sleep(0.1)

    if remains > 0:
        offset -= remains
        ids = get_execution_ids(client, project, job_filter, offset, remains)
        deleted += client.delete_executions(ids, dry_run)

    return deleted


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-j', '--job_filter', type=str, default=None)
    parser.add_argument('-t', '--access_token', type=str,
                        default=None, required=True)
    parser.add_argument('-H', '--host', type=str, default='http://localhost')
    parser.add_argument('-P', '--port', type=int, default=4440)
    parser.add_argument('-k', '--keep_history_size', type=int, default=20)
    parser.add_argument('-m', '--max_delete_size',
                        type=int, default=sys.maxsize)
    parser.add_argument('-c', '--chunk_size', type=int, default=20)
    parser.add_argument('-n', '--dry_run', action='store_true', default=False)
    parser.add_argument('-p', '--project', type=str, required=False)

    return parser.parse_args()


class Client():
    def __init__(self, host, port, access_token):
        self.host = host
        self.port = port
        self.access_token = access_token
        API_VERSION = 18
        self.root = '{host}/api/{version}'.format(
            host=self.host, version=API_VERSION)

    def get(self, path):
        headers = {'X-Rundeck-Auth-Token': self.access_token}
        res = requests.get('{}/{}'.format(self.root, path), headers=headers)
        res.raise_for_status()
        return res

    def post(self, path, **kwargs):
        headers = {'X-Rundeck-Auth-Token': self.access_token}
        res = requests.post('{}/{}'.format(self.root, path),
                            headers=headers, **kwargs)
        res.raise_for_status()
        return res

    def delete_executions(self, ids, dry_run=False):
        logging.info("Purge {} entries: {}".format(len(ids), ids))
        count = 0
        if not dry_run:
            res = self.post('executions/delete', data={"ids": ids})
            fs = etree.fromstring(res.text)
            count = int(fs.find('./successful').get("count"))
            allsuccessful = strtobool(fs.get('allsuccessful'))
            if allsuccessful:
                return count
            messages = set([x.get("message") for x in fs.findall('.//execution')])
            # error example: {'Unauthorized: Delete execution in project xxx'}
            logging.error("error-messages:{} ".format(messages))
        return count

if __name__ == '__main__':
    args = parse_args()

    logging.basicConfig(level=logging.INFO)

    print("Args: \n")
    print("\n".join([
        "\t{}: {}".format(name, getattr(args, name)) for name in vars(args) if name != 'access_token'
    ]))

    client = Client(args.host, args.port, args.access_token)
    if args.project:
        projects = [args.project]
    else:
        projects = get_projects(client)
    for project in projects:
        deleted = purge_history(
            client,
            project,
            args.job_filter,
            args.keep_history_size,
            args.chunk_size,
            args.max_delete_size,
            args.dry_run
        )
        logging.info("Total deleted: {}".format(deleted))
