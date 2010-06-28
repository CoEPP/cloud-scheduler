#!/usr/bin/env python
# vim: set expandtab ts=4 sw=4:

# Copyright (C) 2009 University of Victoria
# You may distribute under the terms of either the GNU General Public
# License or the Apache v2 License, as specified in the README file.

## Auth: Duncan Penfold-Brown. 6/15/2009.

## CLOUD MANAGEMENT
##

##
## IMPORTS
##

import os
import sys
import logging
import threading

import ConfigParser
import cluster_tools
from suds.client import Client
import cloudscheduler.config as config
from urllib2 import URLError
from decimal import *
try:
    import cPickle as pickle
except:
    import pickle

from cloudscheduler.utilities import determine_path
from cloudscheduler.utilities import get_or_none

##
## GLOBALS
##
log = None
log = logging.getLogger("cloudscheduler")

##
## CLASSES
##

# A class that stores and organises a list of Cluster resources

class ResourcePool:

    ## Instance variables
    resources = []

    ## Instance methods

    # Constructor
    # name   - The name of the ResourcePool being created
    def __init__(self, name):
        global log
        log = logging.getLogger("cloudscheduler")
        log.debug("New ResourcePool " + name + " created")
        self.name = name
        self.write_lock = threading.Lock()

        _collector_wsdl = "file://" + determine_path() \
                          + "/wsdl/condorCollector.wsdl"
        self.condor_collector = Client(_collector_wsdl, cache=None, location=config.condor_collector_url)

    # Read in defined clouds from cloud definition file
    def setup(self, config_file):
        #TODO: Merge this with _init_

        log.info("Reading cloud resource configuration file %s" % config_file)
        # Check for config files with ~ in the path
        config_file = os.path.expanduser(config_file)

        cloud_config = ConfigParser.ConfigParser()
        try:
            cloud_config.read(config_file)
        except ConfigParser.ParsingError:
            print >> sys.stderr, "Cloud config problem: Couldn't " \
                  "parse your cloud config file. Check for spaces " \
                  "before or after variables."
            raise


        # Read in config file, parse into Cluster objects
        for cluster in cloud_config.sections():

            cloud_type = get_or_none(cloud_config, cluster, "cloud_type")

            # Create a new cluster according to cloud_type
            if cloud_type == "Nimbus":
                new_cluster = cluster_tools.NimbusCluster(name = cluster,
                               host = get_or_none(cloud_config, cluster, "host"),
                               cloud_type = get_or_none(cloud_config, cluster, "cloud_type"),
                               memory = map(int, get_or_none(cloud_config, cluster, "memory").split(",")),
                               cpu_archs = get_or_none(cloud_config, cluster, "cpu_archs").split(","),
                               networks = get_or_none(cloud_config, cluster, "networks").split(","),
                               vm_slots = int(get_or_none(cloud_config, cluster, "vm_slots")),
                               cpu_cores = int(get_or_none(cloud_config, cluster, "cpu_cores")),
                               storage = int(get_or_none(cloud_config, cluster, "storage")),
                               )

            elif cloud_type == "AmazonEC2" or cloud_type == "Eucalyptus":
                new_cluster = cluster_tools.EC2Cluster(name = cluster,
                               host = get_or_none(cloud_config, cluster, "host"),
                               cloud_type = get_or_none(cloud_config, cluster, "cloud_type"),
                               memory = map(int, get_or_none(cloud_config, cluster, "memory").split(",")),
                               cpu_archs = get_or_none(cloud_config, cluster, "cpu_archs").split(","),
                               networks = get_or_none(cloud_config, cluster, "networks").split(","),
                               vm_slots = int(get_or_none(cloud_config, cluster, "vm_slots")),
                               cpu_cores = int(get_or_none(cloud_config, cluster, "cpu_cores")),
                               storage = int(get_or_none(cloud_config, cluster, "storage")),
                               access_key_id = get_or_none(cloud_config, cluster, "access_key_id"),
                               secret_access_key = get_or_none(cloud_config, cluster, "secret_access_key"),
                               security_group = get_or_none(cloud_config, cluster, "security_group"),
                               )

            else:
                log.error("ResourcePool.setup doesn't know what to do with the"
                          + "%s cloud_type" % cloud_type)
                continue

            # Add the new cluster to a resource pool
            if new_cluster:
                self.add_resource(new_cluster)
        #END For

        self.load_persistence()


    # Add a cluster resource to the pool's resource list
    def add_resource(self, cluster):
        self.resources.append(cluster)

    # Log a list of clusters.
    # Supports independently logging a list of clusters for specific ResourcePool
    # functionality (such a printing intermediate working cluster lists)
    def log_list(self, clusters):
        for cluster in clusters:
            cluster.log()

    # Log the name and address of every cluster in the resource pool
    def log_pool(self, ):
        log.debug(self.get_pool_info())

    # Print the name and address of every cluster in the resource pool
    def get_pool_info(self, ):
        output = "Resource pool " + self.name + ":\n"
        output += "%-15s  %-10s %-15s \n" % ("NAME", "CLOUD TYPE", "NETWORK ADDRESS")
        if len(self.resources) == 0:
            output += "Pool is empty..."
        else:
            for cluster in self.resources:
                output += "%-15s  %-10s %-15s \n" % (cluster.name, cluster.cloud_type, cluster.network_address)
        return output


    # Return an arbitrary resource from the 'resources' list. Does not remove
    # the returned element from the list.
    # (Currently, the first cluster in the list is returned)
    def get_resource(self, ):
        if len(self.resources) == 0:
            log.debug("Pool is empty... Cannot return resource.")
            return None

        return (self.resources[0])



    # Returns a list of Clusters that fit the given VM/Job requirements
    # Parameters: (as for get_resource methods)
    # Return: a list of Cluster objects representing clusters that meet given
    #         requirements for network, cpu, memory, and storage
    def get_fitting_resources(self, network, cpuarch, memory, cpucores, storage):
        if len(self.resources) == 0:
            log.debug("Pool is empty... Cannot return list of fitting resources")
            return []

        fitting_clusters = []
        for cluster in self.resources:
            # If the cluster has no open VM slots
            if (cluster.vm_slots <= 0):
                continue
            # If the cluster does not have the required CPU architecture
            if (cpuarch not in cluster.cpu_archs):
                continue
            # If required network is NOT in cluster's network associations
            if (network not in cluster.network_pools):
                continue
            # If the cluster has no sufficient memory entries for the VM
            if (cluster.find_mementry(memory) < 0):
                continue
            # If the cluster does not have sufficient CPU cores
            if (cpucores > cluster.cpu_cores):
                continue
            # If the cluster does not have sufficient storage capacity
            if (storage > cluster.storageGB):
                continue
            # Add cluster to the list to be returned (meets all job reqs)
            fitting_clusters.append(cluster)

        # Return the list clusters that fit given requirements
        log.debug("List of fitting clusters: ")
        self.log_list(fitting_clusters)
        return fitting_clusters



    # Check that a cluster will be able to meet the static requirements.
    # Parameters:
    #    network  - the network assoication required by the VM
    #    cpuarch  - the cpu architecture that the VM must run on
    # Return: True if cluster is found that fits VM requirments
    #         Otherwise, returns False
    def resourcePF(self, network, cpuarch):
        potential_fit = False

        for cluster in self.resources:
            # If the cluster does not have the required CPU architecture
            if not (cpuarch in cluster.cpu_archs):
                continue
            # If required network is NOT in cluster's network associations
            if not (network in cluster.network_pools):
                continue
            # Cluster meets network and cpu reqs
            potential_fit = True
            break

        # If no clusters are found (no clusters can host the required VM)
        return potential_fit


    # Return cluster that matches cluster_name
    def get_cluster(self, cluster_name):
        for cluster in self.resources:
            if cluster.name == cluster_name:
                return cluster
        return None

    # Find cluster that contains vm
    def get_cluster_with_vm(self, vm):
        cluster = None
        for c in self.resources:
            if vm in c.vms:
                cluster = c
        return cluster

    # Convert the Condor class ad struct into a python dict
    # Note this is done 'stupidly' without checking data types
    def convert_classad_dict(self, ad):
        native = {}
        attrs = ad[0]
        for attr in attrs:
            if attr.name and attr.value:
                native[attr.name] = attr.value
        return native

    # Takes a list of Condor class ads to convert
    def convert_classad_list(self, ad):
        native_list = []
        items = ad[0]
        for item in items:
            native_list.append(self.convert_classad_dict(item))
        return native_list

    # SOAP Query to the condor collector
    # Returns a list of dictionaries with information about the machines
    # registered with condor.
    def resource_querySOAP(self):
        log.debug("Querying condor startd with SOAP API")
        try:
            machines = self.condor_collector.service.queryStartdAds()
            if len(machines) != 0:
                machineList = self.convert_classad_list(machines)
            else:
                machineList = None
            return machineList

        except URLError, e:
            log.error("There was a problem connecting to the "
                      "Condor scheduler web service (%s) for the following "
                      "reason: %s"
                      % (config.condor_collector_url, e.reason[1]))
        except:
            log.error("There was a problem connecting to the "
                      "Condor scheduler web service (%s)"
                      % (config.condor_collector_url))

    # Get a Dictionary of required VM Types with how many of that type running
    # Uses the dict-list structure returned by SOAP query
    def get_vmtypes_count(self, machineList):
        count = {}
        for vm in machineList:
            if vm.has_key('VMType'):
                if vm['VMType'] not in count:
                    count[vm['VMType']] = 1
                else:
                    count[vm['VMType']] += 1
        return count

    # Determines if the key value pairs in in criteria are in the dictionary
    def match_criteria(self, base, criteria):
        return criteria == dict(set(base.items()).intersection(set(criteria.items())))
    # Find all the matching entries for given criteria
    def find_in_where(self, machineList, criteria):
        matches = []
        for machine in machineList:
            if self.match_criteria(machine, criteria):
                matches.append(machine)
        return matches

    # Get a dictionary of types of VMs the scheduler is currently tracking
    def get_vmtypes_count_internal(self):
        types = {}
        for cluster in self.resources:
            for vm in cluster.vms:
                if vm.vmtype in types:
                    types[vm.vmtype] += 1
                else:
                    types[vm.vmtype] = 1
        return types

    # Count of VMs in the system
    def vm_count(self):
        count = 0
        for cluster in self.resources:
            count = count + len(cluster.vms)
        return count

    # VM Type Distribution
    def vmtype_distribution(self):
        types = self.get_vmtypes_count_internal()
        count = Decimal(self.vm_count())
        if count == 0:
            return {}
        count = 1 / count
        for type in types.keys():
            types[type] *= count
        return types

    # Take the current and previous machineLists
    # Figure out which machines have changed jobs
    # return list of machine names that have
    def machine_jobs_changed(self, current, previous):
        auxCurrent = dict((d['Name'], d['GlobalJobId']) for d in current if 'GlobalJobId' in d.keys())
        auxPrevious = dict((d['Name'], d['GlobalJobId']) for d in previous if 'GlobalJobId' in d.keys())
        changed = [k for k,v in auxPrevious.items() if k in auxCurrent and auxCurrent[k] != v]
        for n in range(0, len(changed)):
            changed[n] = changed[n].split('.')[0]
        return changed

    def save_persistence(self):
        """
        save_persistence - pickle the resources list to the persistence file
        """
        try:
            persistence_file = open(config.persistence_file, "wb")
            pickle.dump(self.resources, persistence_file)
            persistence_file.close()
        except IOError, e:

            log.error("Couldn't write persistence file to %s! \"%s\"" % 
                      (config.persistence_file, e.strerror))
        except:
            log.exception("Unknown problem saving persistence file!")

    def load_persistence(self):
        """
        load_persistence - if a pickled persistence file exists, load it and 
                           check to see if the resources described in it are
                           valid. If so, add them to the list of resources.
        """

        try:
            log.info("Loading persistence file from last run.")
            persistence_file = open(config.persistence_file, "rb")
        except IOError, e:
            log.debug("No persistence file to load. Exited normally last time.")
            return

        old_resources = pickle.load(persistence_file)
        persistence_file.close()

        for old_cluster in old_resources:
            old_cluster.setup_logging()

            for vm in old_cluster.vms:

                log.debug("Found VM %s" % vm.id)
                if old_cluster.vm_poll(vm) != "Error":
                    new_cluster = self.get_cluster(old_cluster.name)

                    if new_cluster:
                        new_cluster.vms.append(vm)
                        new_cluster.resource_checkout(vm)
                        log.info("Persisted VM %s on %s." % (vm.id, new_cluster.name))
                    else:
                        log.info("%s doesn't seem to exist, so destroying vm %s." %
                                 (old_cluster.name, vm.id))
                        old_cluster.vm_destroy(vm)
                else:
                    log.info("Found persisted VM %s from %s in an error state, destroying it." %
                             (vm.id, old_cluster.name))
                    old_cluster.vm_destroy(vm)
