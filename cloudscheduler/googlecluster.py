import os
import time
import threading
import nimbus_xml
import ConfigParser
import cluster_tools
import cloudscheduler.utilities as utilities
try:
    import httplib2
    from oauth2client.client import flow_from_clientsecrets
    from oauth2client.file import Storage
    from oauth2client.tools import run
    from apiclient.discovery import build
except:
    pass

log = utilities.get_cloudscheduler_logger()


class GoogleComputeEngineCluster(cluster_tools.ICluster):
    GCE_SCOPE = 'https://www.googleapis.com/auth/compute'
    
    API_VERSION = 'v1beta13'
    GCE_URL = 'https://www.googleapis.com/compute/%s/projects/' % (API_VERSION)

    DEFAULT_ZONE = 'us-central1-a' # will need to be option in job
    DEFAULT_MACHINE_TYPE = 'n1-standard-1'  # option specified in job config
    DEFAULT_IMAGE = 'condorimagebase'  

    DEFAULT_NETWORK = 'default' # job option setup
    DEFAULT_SERVICE_EMAIL = 'default' 
    DEFAULT_SCOPES = ['https://www.googleapis.com/auth/devstorage.full_control',
                  'https://www.googleapis.com/auth/compute']

    def __init__(self, name="Dummy Cluster", host="localhost",
                 cloud_type="Dummy", memory=[], max_vm_mem= -1, cpu_archs=[], networks=[],
                 vm_slots=0, cpu_cores=0, storage=0, hypervisor='xen', boot_timeout=None,
                 auth_dat_file=None, secret_file=None, security_group=None, project_id=None):

        self.gce_hostname_prefix = 'gce-cs-vm'
        self.gce_hostname_counter = 0
        self.security_group = security_group
        self.auth_dat_file_path = auth_dat_file
        self.secret_file_path = secret_file
        self.project_id = project_id
        if not project_id:
            return None
        
        
        # Perform OAuth 2.0 authorization.
        flow = flow_from_clientsecrets(self.secret_file_path, scope=self.GCE_SCOPE)
        auth_storage = Storage(self.auth_dat_file_path)
        credentials = auth_storage.get()
      
        if credentials is None or credentials.invalid:
            credentials = run(flow, auth_storage)
        http = httplib2.Http()
        self.auth_http = credentials.authorize(http)


        # Build service object
        self.gce_service = build('compute', self.API_VERSION)
        self.project_url = self.GCE_URL + self.project_id
        # Call super class's init
        cluster_tools.ICluster.__init__(self,name=name, host=host, cloud_type=cloud_type,
                         memory=memory, max_vm_mem=max_vm_mem, cpu_archs=cpu_archs, networks=networks,
                         vm_slots=vm_slots, cpu_cores=cpu_cores,
                         storage=storage, hypervisor=hypervisor, boot_timeout=boot_timeout)

    def vm_create(self, vm_name, vm_type, vm_user, vm_networkassoc, vm_cpuarch,
                  vm_image, vm_mem, vm_cores, vm_storage, customization=None,
                  vm_keepalive=0, instance_type="", maximum_price=0,
                  job_per_core=False, securitygroup=[]):
        try:
            vm_ami = vm_image[self.network_address]
        except:
            log.debug("No AMI for %s, trying default" % self.network_address)
            try:
                vm_ami = vm_image["default"]
            except:
                log.exception("Can't find a suitable AMI")
                return
        # Construct URLs
        if instance_type:
            vm_instance_type = instance_type
        else:
            vm_instance_type = self.DEFAULT_MACHINE_TYPE
        if vm_image:
            vm_image_name = vm_ami
        else:
            vm_image_name = self.DEFAULT_IMAGE

        image_url = '%s%s/images/%s' % (
               self.GCE_URL, self.project_id, vm_image_name)
        machine_type_url = '%s/machineTypes/%s' % (
              self.project_url, vm_instance_type)
        zone_url = '%s/zones/%s' % (self.project_url, self.DEFAULT_ZONE)
        network_url = '%s/networks/%s' % (self.project_url, self.DEFAULT_NETWORK)

        if customization:
            user_data = nimbus_xml.ws_optional(customization)
        else:
            user_data = ""

        next_instance_name = self.generate_next_instance_name()
        # Construct the request body
        instance = {
          'name': next_instance_name,
          'machineType': machine_type_url,
          'image': image_url,
          'zone': zone_url,
          'networkInterfaces': [{
            'accessConfigs': [{
              'type': 'ONE_TO_ONE_NAT',
              'name': 'External NAT'
             }],
            'network': network_url
          }],
          'serviceAccounts': [{
               'email': self.DEFAULT_SERVICE_EMAIL,
               'scopes': self.DEFAULT_SCOPES
          }],
          'metadata': {
              'items': [{
                  'key': 'userdata',
                  'value': user_data,
              }]
          }
        }

        # Create the instance
        request = self.gce_service.instances().insert(
             project=self.project_id, body=instance)
        response = request.execute(self.auth_http)
        response = self._blocking_call(self.gce_service, self.auth_http, response)

        if 'targetId' in response:
            target_id = response['targetId']
        else:
            print 'targetID missing'
            print response
            return
        vm_mementry = self.find_mementry(vm_mem)
        if (vm_mementry < 0):
            #TODO: this is kind of pointless with EC2..., but the resource code depends on it
            log.debug("Cluster memory list has no sufficient memory " +\
                      "entries (Not supposed to happen). Returning error.")
            return self.ERROR
        new_vm = cluster_tools.VM(name = next_instance_name, vmtype = vm_type, user = vm_user,
                    clusteraddr = self.network_address, id = target_id,
                    cloudtype = self.cloud_type, network = vm_networkassoc,
                    hostname = self.construct_hostname(next_instance_name),
                    cpuarch = vm_cpuarch, image= vm_image, mementry = vm_mementry,
                    memory = vm_mem, cpucores = vm_cores, storage = vm_storage, 
                    keep_alive = vm_keepalive, job_per_core = job_per_core)
    
        try:
            self.resource_checkout(new_vm)
        except:
            log.exception("Unexpected Error checking out resources when creating a VM. Programming error?")
            self.vm_destroy(new_vm, reason="Failed Resource checkout")
            return self.ERROR
    
        self.vms.append(new_vm)
        return 0


    def vm_destroy(self, vm, return_resources=True, reason=""):
        # Delete an Instance
        request = self.gce_service.instances().delete(
            project=self.project_id, instance=vm.name)
        response = request.execute(self.auth_http)
        response = self._blocking_call(self.gce_service, self.auth_http, response)

        # Delete references to this VM
        if return_resources:
            self.resource_return(vm)
        with self.vms_lock:
            self.vms.remove(vm)
        pass

    def vm_poll(self, vm):
        #filter_str = ''.join(["id eq ", vm.id])
        #request = self.gce_service.instances().list(project=self.project_id, filter=filter_str)
        request = self.gce_service.instances().list(project=self.project_id)

        response = request.execute(self.auth_http)

        if response and 'items' in response:
            instances = response['items']
            for instance in instances:
                if 'id' in instance and instance['id'] == vm.id:
                    vm.status = instance['status']
                    vm.ipaddress = instance['networkInterfaces'][0]['accessConfigs'][0]['natIP']
                
        pass


    def _blocking_call(self, gce_service, auth_http, response):
        """Blocks until the operation status is done for the given operation."""
        if 'status' in response:
            status = response['status']
        failed_status = 0
        while status != 'DONE' and response:
            if 'name' in response:
                operation_id = response['name']
            else:
                break
            request = gce_service.operations().get(
                project=self.project_id, operation=operation_id)
            response = request.execute(auth_http)
            if response and 'status' in response:
                status = response['status']
            #else:
                #failed_status += 1
                #if failed_status > 10:
                    #return response
            time.sleep(1)
        return response
    
    def generate_next_instance_name(self):
        for x in range(0,10):
            potential_name = ''.join([self.gce_hostname_prefix, str(self.gce_hostname_counter)])
            self.gce_hostname_counter += 1
            if self.gce_hostname_counter >= 50000:
                self.gce_hostname_counter = 0
            collision = False
            for vm in self.vms:
                if potential_name == vm.name:
                    collision = True
                    break
            if not collision:
                break
        # had 10 collisions give up and try again later
        if collision:
            potential_name = None
        return potential_name
    def construct_hostname(self, instance_name):
        return ''.join([instance_name, '.c.', self.project_id, '.internal'])
            
