'''
Copyright (c) 2017 VMware, Inc. All Rights Reserved.
SPDX-License-Identifier: BSD-2-Clause
'''
import logging
import subprocess

from classes.docker_image import DockerImage
from classes.notice import Notice
from classes.package import Package
from utils import dockerfile as df
from utils import container as cont
from utils import constants as const
from report import errors
from command_lib import command_lib as cmdlib
import common

'''
Docker specific functions - used when trying to retrieve packages when
given a Dockerfile
'''

# dockerfile
dockerfile = ''
# dockerfile commands
docker_commands = []

# global logger
logger = logging.getLogger('docker.py')


def load_docker_commands(dockerfile_path):
    '''Given a dockerfile get a persistent list of docker commands'''
    global docker_commands
    docker_commands = df.get_directive_list(df.get_command_list(
        dockerfile_path))
    global dockerfile
    dockerfile = dockerfile_path


def print_dockerfile_base(base_instructions):
    '''For the purpose of tracking the lines in the dockerfile that
    produce packages, return a string containing the lines in the dockerfile
    that pertain to the base image'''
    base_instr = ''
    for instr in base_instructions:
        base_instr = base_instr + instr[0] + ' ' + instr[1] + '\n'
    return base_instr


def get_dockerfile_base():
    '''Get the base image object from the dockerfile base instructions
    1. get the instructions around FROM
    2. get the base image and tag
    3. Make notes based on what the image and tag rules are
    4. Return an image object and the base instructions string'''
    try:
        base_instructions = df.get_base_instructions(docker_commands)
        base_image_tag = df.get_base_image_tag(base_instructions)
        # check for scratch
        if base_image_tag[0] == 'scratch':
            # there is no base image - return no image object
            return None
        # there should be some image object here
        repotag = base_image_tag[0] + df.tag_separator + base_image_tag[1]
        from_line = 'FROM ' + repotag
        origin = print_dockerfile_base(base_instructions)
        base_image = DockerImage(repotag)
        base_image.name = base_image_tag[0]
        # check if there is a tag
        if not base_image_tag[1]:
            message_string = errors.dockerfile_no_tag.format(
                dockerfile_line=from_line)
            no_tag_notice = Notice()
            no_tag_notice.origin = origin
            no_tag_notice.message = message_string
            no_tag_notice.level = 'warning'
            base_image.notices.add_notice(no_tag_notice)
            base_image.tag = 'latest'
        else:
            base_image.tag = base_image_tag[1]
        # check if the tag is 'latest'
        if base_image_tag[1] == 'latest':
            message_string = errors.dockerfile_using_latest.format(
                dockerfile_line=from_line)
            latest_tag_notice = Notice()
            latest_tag_notice.origin = origin
            no_tag_notice.message = message_string
            no_tag_notice.level = 'warning'
            base_image.notices.add_notice(latest_tag_notice)
        return base_image, base_instructions
    except ValueError as e:
        logger.warning(errors.cannot_parse_base_image.format(
            dockerfile=dockerfile, error_msg=e))
        return None


def get_dockerfile_image_tag():
    '''Return the image and tag used to build an image from the dockerfile'''
    image_tag_string = const.image + df.tag_separator + const.tag
    return image_tag_string


def is_build():
    '''Attempt to build a given dockerfile
    If it does not build return False. Else return True'''
    image_tag_string = get_dockerfile_image_tag()
    success = False
    msg = ''
    try:
        cont.build_container(dockerfile, image_tag_string)
    except subprocess.CalledProcessError as error:
        print(errors.docker_build_failed.format(
            dockerfile=dockerfile, error_msg=error.output))
        success = False
        msg = error.output
    else:
        success = True
    return success, msg


def add_packages_from_history(image_obj, shell):
    '''Given a DockerImage object, get package objects installed in each layer
    Assume that the imported images have already gone through this process and
    have their layer's packages populated. So collecting package object occurs
    from the last linked layer:
        1. For each layer get a list of package names
        2. For each package name get a list of dependencies
        3. Create a list of package objects with metadata
        4. Add this to the layer'''
    image_layers = image_obj.layers[image_obj.get_last_import_layer():]
    for layer in image_layers:
        if 'RUN' in layer.created_by:
            origin = layer.diff_id + ': ' + layer.created_by
            run_command_line = layer.created_by.split(' ', 1)[1]
            cmd_list, msg = common.filter_install_commands(run_command_line)
            if msg:
                cmd_parse_notice = Notice(origin, msg, 'warning')
                layer.add_notice(cmd_parse_notice)
            for command in cmd_list:
                pkg_list = common.get_installed_package_names(command)
                all_pkgs = []
                for pkg_name in pkg_list:
                    pkg_listing = cmdlib.get_package_listing(
                        command.name, pkg_name)
                    deps, deps_msg = common.get_package_dependencies(
                        pkg_listing, pkg_name, shell)
                    if deps_msg:
                        logger.warning(deps_msg)
                    all_pkgs.append(pkg_name)
                    all_pkgs.extend(deps)
                unique_pkgs = list(set(all_pkgs))
                for pkg_name in unique_pkgs:
                    pkg = Package(pkg_name)
                    pkg_listing = cmdlib.get_package_listing(
                        command.name, pkg_name)
                    common.fill_package_metadata(pkg, pkg_listing, shell)
                    layer.add_package(pkg)
