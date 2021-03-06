from os.path import join, dirname, isdir, exists, isfile
import importlib
import zipfile
import glob
from six import PY2

import sh
import shutil
from os import listdir, unlink, environ, mkdir
from sys import stdout
try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse
from pythonforandroid.logger import (logger, info, warning, shprint, info_main)
from pythonforandroid.util import (urlretrieve, current_directory, ensure_dir)

# this import is necessary to keep imp.load_source from complaining :)
import pythonforandroid.recipes


if PY2:
    import imp
    import_recipe = imp.load_source
else:
    import importlib.util
    if hasattr(importlib.util, 'module_from_spec'):
        def import_recipe(module, filename):
            spec = importlib.util.spec_from_file_location(module, filename)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    else:
        from importlib.machinery import SourceFileLoader

        def import_recipe(module, filename):
            return SourceFileLoader(module, filename).load_module()


class Recipe(object):
    url = None
    '''The address from which the recipe may be downloaded. This is not
    essential, it may be omitted if the source is available some other
    way, such as via the :class:`IncludedFilesBehaviour` mixin.

    If the url includes the version, you may (and probably should)
    replace this with ``{version}``, which will automatically be
    replaced by the :attr:`version` string during download.

    .. note:: Methods marked (internal) are used internally and you
              probably don't need to call them, but they are available
              if you want.
    '''

    version = None
    '''A string giving the version of the software the recipe describes,
    e.g. ``2.0.3`` or ``master``.'''

    md5sum = None
    '''The md5sum of the source from the :attr:`url`. Non-essential, but
    you should try to include this, it is used to check that the download
    finished correctly.
    '''

    depends = []
    '''A list containing the names of any recipes that this recipe depends on.
    '''

    conflicts = []
    '''A list containing the names of any recipes that are known to be
    incompatible with this one.'''

    opt_depends = []
    '''A list of optional dependencies, that must be built before this
    recipe if they are built at all, but whose presence is not essential.'''

    patches = []
    '''A list of patches to apply to the source. Values can be either a string
    referring to the patch file relative to the recipe dir, or a tuple of the
    string patch file and a callable, which will receive the kwargs `arch` and
    `recipe`, which should return True if the patch should be applied.'''

    archs = ['armeabi']  # Not currently implemented properly

    @property
    def versioned_url(self):
        '''A property returning the url of the recipe with ``{version}``
        replaced by the :attr:`url`. If accessing the url, you should use this
        property, *not* access the url directly.'''
        if self.url is None:
            return None
        return self.url.format(version=self.version)

    def download_file(self, url, target, cwd=None):
        """
        (internal) Download an ``url`` to a ``target``.
        """
        if not url:
            return
        info('Downloading {} from {}'.format(self.name, url))

        if cwd:
            target = join(cwd, target)

        parsed_url = urlparse(url)
        if parsed_url.scheme in ('http', 'https'):
            def report_hook(index, blksize, size):
                if size <= 0:
                    progression = '{0} bytes'.format(index * blksize)
                else:
                    progression = '{0:.2f}%'.format(
                        index * blksize * 100. / float(size))
                stdout.write('- Download {}\r'.format(progression))
                stdout.flush()

            if exists(target):
                unlink(target)

            urlretrieve(url, target, report_hook)
            return target
        elif parsed_url.scheme in ('git', 'git+ssh', 'git+http', 'git+https'):
            if isdir(target):
                with current_directory(target):
                    shprint(sh.git, 'fetch', '--tags')
                    if self.version:
                        shprint(sh.git, 'checkout', self.version)
                    shprint(sh.git, 'pull')
                    shprint(sh.git, 'pull', '--recurse-submodules')
                    shprint(sh.git, 'submodule', 'update', '--recursive')
            else:
                if url.startswith('git+'):
                    url = url[4:]
                shprint(sh.git, 'clone', '--recursive', url, target)
                if self.version:
                    with current_directory(target):
                        shprint(sh.git, 'checkout', self.version)
                        shprint(sh.git, 'submodule', 'update', '--recursive')
            return target

    def extract_source(self, source, cwd):
        """
        (internal) Extract the `source` into the directory `cwd`.
        """
        if not source:
            return
        if isfile(source):
            info("Extract {} into {}".format(source, cwd))

            if source.endswith(".tgz") or source.endswith(".tar.gz"):
                shprint(sh.tar, "-C", cwd, "-xvzf", source)

            elif source.endswith(".tbz2") or source.endswith(".tar.bz2"):
                shprint(sh.tar, "-C", cwd, "-xvjf", source)

            elif source.endswith(".zip"):
                zf = zipfile.ZipFile(source)
                zf.extractall(path=cwd)
                zf.close()

            else:
                warning(
                    "Error: cannot extract, unrecognized extension for {}"
                    .format(source))
                raise Exception()

        elif isdir(source):
            info("Copying {} into {}".format(source, cwd))

            shprint(sh.cp, '-a', source, cwd)

        else:
            warning(
                "Error: cannot extract or copy, unrecognized path {}"
                .format(source))
            raise Exception()

    # def get_archive_rootdir(self, filename):
    #     if filename.endswith(".tgz") or filename.endswith(".tar.gz") or \
    #         filename.endswith(".tbz2") or filename.endswith(".tar.bz2"):
    #         archive = tarfile.open(filename)
    #         root = archive.next().path.split("/")
    #         return root[0]
    #     elif filename.endswith(".zip"):
    #         with zipfile.ZipFile(filename) as zf:
    #             return dirname(zf.namelist()[0])
    #     else:
    #         print("Error: cannot detect root directory")
    #         print("Unrecognized extension for {}".format(filename))
    #         raise Exception()

    def apply_patch(self, filename, arch):
        """
        Apply a patch from the current recipe directory into the current
        build directory.
        """
        info("Applying patch {}".format(filename))
        filename = join(self.recipe_dir, filename)
        shprint(sh.patch, "-t", "-d", self.get_build_dir(arch), "-p1",
                "-i", filename, _tail=10)

    def copy_file(self, filename, dest):
        info("Copy {} to {}".format(filename, dest))
        filename = join(self.recipe_dir, filename)
        dest = join(self.build_dir, dest)
        shutil.copy(filename, dest)

    def append_file(self, filename, dest):
        info("Append {} to {}".format(filename, dest))
        filename = join(self.recipe_dir, filename)
        dest = join(self.build_dir, dest)
        with open(filename, "rb") as fd:
            data = fd.read()
        with open(dest, "ab") as fd:
            fd.write(data)

    # def has_marker(self, marker):
    #     """
    #     Return True if the current build directory has the marker set
    #     """
    #     return exists(join(self.build_dir, ".{}".format(marker)))

    # def set_marker(self, marker):
    #     """
    #     Set a marker info the current build directory
    #     """
    #     with open(join(self.build_dir, ".{}".format(marker)), "w") as fd:
    #         fd.write("ok")

    # def delete_marker(self, marker):
    #     """
    #     Delete a specific marker
    #     """
    #     try:
    #         unlink(join(self.build_dir, ".{}".format(marker)))
    #     except:
    #         pass

    @property
    def name(self):
        '''The name of the recipe, the same as the folder containing it.'''
        modname = self.__class__.__module__
        return modname.split(".", 2)[-1]

    # @property
    # def archive_fn(self):
    #     bfn = basename(self.url.format(version=self.version))
    #     fn = "{}/{}-{}".format(
    #         self.ctx.cache_dir,
    #         self.name, bfn)
    #     return fn

    @property
    def filtered_archs(self):
        '''Return archs of self.ctx that are valid build archs
        for the Recipe.'''
        result = []
        for arch in self.ctx.archs:
            if not self.archs or (arch.arch in self.archs):
                result.append(arch)
        return result

    def check_recipe_choices(self):
        '''Checks what recipes are being built to see which of the alternative
        and optional dependencies are being used,
        and returns a list of these.'''
        recipes = []
        built_recipes = self.ctx.recipe_build_order
        for recipe in self.depends:
            if isinstance(recipe, (tuple, list)):
                for alternative in recipe:
                    if alternative in built_recipes:
                        recipes.append(alternative)
                        break
        for recipe in self.opt_depends:
            if recipe in built_recipes:
                recipes.append(recipe)
        return sorted(recipes)

    def get_build_container_dir(self, arch):
        '''Given the arch name, returns the directory where it will be
        built.

        This returns a different directory depending on what
        alternative or optional dependencies are being built.
        '''
        dir_name = self.get_dir_name()
        return join(self.ctx.build_dir, 'other_builds', dir_name, arch)

    def get_dir_name(self):
        choices = self.check_recipe_choices()
        dir_name = '-'.join([self.name] + choices)
        return dir_name

    def get_build_dir(self, arch):
        '''Given the arch name, returns the directory where the
        downloaded/copied package will be built.'''

        return join(self.get_build_container_dir(arch), self.name)

    def get_recipe_dir(self):
        # AND: Redundant, an equivalent property is already set by get_recipe
        return join(self.ctx.root_dir, 'recipes', self.name)

    # Public Recipe API to be subclassed if needed

    def download_if_necessary(self):
        info_main('Downloading {}'.format(self.name))
        user_dir = environ.get('P4A_{}_DIR'.format(self.name.lower()))
        if user_dir is not None:
            info('P4A_{}_DIR is set, skipping download for {}'.format(
                self.name, self.name))
            return
        self.download()

    def download(self):
        if self.url is None:
            info('Skipping {} download as no URL is set'.format(self.name))
            return

        url = self.versioned_url

        shprint(sh.mkdir, '-p', join(self.ctx.packages_path, self.name))

        with current_directory(join(self.ctx.packages_path, self.name)):
            filename = shprint(sh.basename, url).stdout[:-1].decode('utf-8')

            do_download = True

            marker_filename = '.mark-{}'.format(filename)
            if exists(filename) and isfile(filename):
                if not exists(marker_filename):
                    shprint(sh.rm, filename)
                elif self.md5sum:
                    current_md5 = shprint(sh.md5sum, filename)
                    print('downloaded md5: {}'.format(current_md5))
                    print('expected md5: {}'.format(self.md5sum))
                    print('md5 not handled yet, exiting')
                    exit(1)
                else:
                    do_download = False
                    info('{} download already cached, skipping'
                         .format(self.name))

            # Should check headers here!
            warning('Should check headers here! Skipping for now.')

            # If we got this far, we will download
            if do_download:
                print('Downloading {} from {}'.format(self.name, url))

                shprint(sh.rm, '-f', marker_filename)
                self.download_file(url, filename)
                shprint(sh.touch, marker_filename)

                if self.md5sum is not None:
                    print('downloaded md5: {}'.format(current_md5))
                    print('expected md5: {}'.format(self.md5sum))
                    print('md5 not handled yet, exiting')
                    exit(1)

    def unpack(self, arch):
        info_main('Unpacking {} for {}'.format(self.name, arch))

        build_dir = self.get_build_container_dir(arch)

        user_dir = environ.get('P4A_{}_DIR'.format(self.name.lower()))
        if user_dir is not None:
            info('P4A_{}_DIR exists, symlinking instead'.format(
                self.name.lower()))
            # AND: Currently there's something wrong if I use ln, fix this
            warning('Using git clone instead of symlink...fix this!')
            if exists(self.get_build_dir(arch)):
                return
            shprint(sh.rm, '-rf', build_dir)
            shprint(sh.mkdir, '-p', build_dir)
            shprint(sh.rmdir, build_dir)
            ensure_dir(build_dir)
            shprint(sh.git, 'clone', user_dir, self.get_build_dir(arch))
            return

        if self.url is None:
            info('Skipping {} unpack as no URL is set'.format(self.name))
            return

        filename = shprint(
            sh.basename, self.versioned_url).stdout[:-1].decode('utf-8')

        with current_directory(build_dir):
            directory_name = self.get_build_dir(arch)

            # AND: Could use tito's get_archive_rootdir here
            if not exists(directory_name) or not isdir(directory_name):
                extraction_filename = join(
                    self.ctx.packages_path, self.name, filename)
                if isfile(extraction_filename):
                    if extraction_filename.endswith('.tar.gz') or \
                       extraction_filename.endswith('.tgz'):
                        sh.tar('xzf', extraction_filename)
                        root_directory = shprint(
                            sh.tar, 'tzf', extraction_filename).stdout.decode(
                                'utf-8').split('\n')[0].split('/')[0]
                        if root_directory != directory_name:
                            shprint(sh.mv, root_directory, directory_name)
                    elif (extraction_filename.endswith('.tar.bz2') or
                          extraction_filename.endswith('.tbz2')):
                        info('Extracting {} at {}'
                             .format(extraction_filename, filename))
                        sh.tar('xjf', extraction_filename)
                        root_directory = sh.tar(
                            'tjf', extraction_filename).stdout.decode(
                                'utf-8').split('\n')[0].split('/')[0]
                        if root_directory != directory_name:
                            shprint(sh.mv, root_directory, directory_name)
                    elif extraction_filename.endswith('.zip'):
                        sh.unzip(extraction_filename)
                        import zipfile
                        fileh = zipfile.ZipFile(extraction_filename, 'r')
                        root_directory = fileh.filelist[0].filename.strip('/')
                        if root_directory != directory_name:
                            shprint(sh.mv, root_directory, directory_name)
                    else:
                        raise Exception(
                            'Could not extract {} download, it must be .zip, '
                            '.tar.gz or .tar.bz2')
                elif isdir(extraction_filename):
                    mkdir(directory_name)
                    for entry in listdir(extraction_filename):
                        if entry not in ('.git',):
                            shprint(sh.cp, '-Rv',
                                    join(extraction_filename, entry),
                                    directory_name)
                else:
                    raise Exception(
                        'Given path is neither a file nor a directory: {}'
                        .format(extraction_filename))

            else:
                info('{} is already unpacked, skipping'.format(self.name))

    def get_recipe_env(self, arch=None):
        """Return the env specialized for the recipe
        """
        if arch is None:
            arch = self.filtered_archs[0]
        return arch.get_env()

    def prebuild_arch(self, arch):
        '''Run any pre-build tasks for the Recipe. By default, this checks if
        any prebuild_archname methods exist for the archname of the current
        architecture, and runs them if so.'''
        prebuild = "prebuild_{}".format(arch.arch)
        if hasattr(self, prebuild):
            getattr(self, prebuild)()
        else:
            info('{} has no {}, skipping'.format(self.name, prebuild))

    def is_patched(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        return exists(join(build_dir, '.patched'))

    def apply_patches(self, arch):
        '''Apply any patches for the Recipe.'''
        if self.patches:
            info_main('Applying patches for {}[{}]'
                      .format(self.name, arch.arch))

            if self.is_patched(arch):
                info_main('{} already patched, skipping'.format(self.name))
                return

            for patch in self.patches:
                if isinstance(patch, (tuple, list)):
                    patch, patch_check = patch
                    if not patch_check(arch=arch, recipe=self):
                        continue

                self.apply_patch(
                        patch.format(version=self.version, arch=arch.arch),
                        arch.arch)

            shprint(sh.touch, join(self.get_build_dir(arch.arch), '.patched'))

    def should_build(self, arch):
        '''Should perform any necessary test and return True only if it needs
        building again.

        '''
        return True

    def build_arch(self, arch):
        '''Run any build tasks for the Recipe. By default, this checks if
        any build_archname methods exist for the archname of the current
        architecture, and runs them if so.'''
        build = "build_{}".format(arch.arch)
        if hasattr(self, build):
            getattr(self, build)()

    def postbuild_arch(self, arch):
        '''Run any post-build tasks for the Recipe. By default, this checks if
        any postbuild_archname methods exist for the archname of the
        current architecture, and runs them if so.
        '''
        postbuild = "postbuild_{}".format(arch.arch)
        if hasattr(self, postbuild):
            getattr(self, postbuild)()

    def prepare_build_dir(self, arch):
        '''Copies the recipe data into a build dir for the given arch. By
        default, this unpacks a downloaded recipe. You should override
        it (or use a Recipe subclass with different behaviour) if you
        want to do something else.
        '''
        self.unpack(arch)

    def clean_build(self, arch=None):
        '''Deletes all the build information of the recipe.

        If arch is not None, only this arch dir is deleted. Otherwise
        (the default) all builds for all archs are deleted.

        By default, this just deletes the main build dir. If the
        recipe has e.g. object files biglinked, or .so files stored
        elsewhere, you should override this method.

        This method is intended for testing purposes, it may have
        strange results. Rebuild everything if this seems to happen.

        '''
        if arch is None:
            dir = join(self.ctx.build_dir, 'other_builds', self.name)
        else:
            dir = self.get_build_container_dir(arch)
        if exists(dir):
            shutil.rmtree(dir)
        else:
            warning(('Attempted to clean build for {} but build '
                     'did not exist').format(self.name))

    @classmethod
    def recipe_dirs(cls, ctx):
        return [ctx.local_recipes,
                join(ctx.storage_dir, 'recipes'),
                join(ctx.root_dir, "recipes")]

    @classmethod
    def list_recipes(cls, ctx):
        forbidden_dirs = ('__pycache__', )
        for recipes_dir in cls.recipe_dirs(ctx):
            if recipes_dir and exists(recipes_dir):
                for name in listdir(recipes_dir):
                    if name in forbidden_dirs:
                        continue
                    fn = join(recipes_dir, name)
                    if isdir(fn):
                        yield name

    @classmethod
    def get_recipe(cls, name, ctx):
        '''Returns the Recipe with the given name, if it exists.'''
        if not hasattr(cls, "recipes"):
            cls.recipes = {}
        if name in cls.recipes:
            return cls.recipes[name]

        recipe_file = None
        for recipes_dir in cls.recipe_dirs(ctx):
            recipe_file = join(recipes_dir, name, '__init__.py')
            if exists(recipe_file):
                break
            recipe_file = None

        if not recipe_file:
            raise IOError('Recipe folder does not exist')

        mod = import_recipe('pythonforandroid.recipes.{}'.format(name), recipe_file)
        if len(logger.handlers) > 1:
            logger.removeHandler(logger.handlers[1])
        recipe = mod.recipe
        recipe.recipe_dir = dirname(recipe_file)
        recipe.ctx = ctx
        cls.recipes[name] = recipe
        return recipe


class IncludedFilesBehaviour(object):
    '''Recipe mixin class that will automatically unpack files included in
    the recipe directory.'''
    src_filename = None

    def prepare_build_dir(self, arch):
        if self.src_filename is None:
            print('IncludedFilesBehaviour failed: no src_filename specified')
            exit(1)
        shprint(sh.cp, '-a', join(self.get_recipe_dir(), self.src_filename),
                self.get_build_dir(arch))


class BootstrapNDKRecipe(Recipe):
    '''A recipe class for recipes built in an Android project jni dir with
    an Android.mk. These are not cached separatly, but built in the
    bootstrap's own building directory.

    To build an NDK project which is not part of the bootstrap, see
    :class:`~pythonforandroid.recipe.NDKRecipe`.
    '''

    dir_name = None  # The name of the recipe build folder in the jni dir

    def get_build_container_dir(self, arch):
        return self.get_jni_dir()

    def get_build_dir(self, arch):
        if self.dir_name is None:
            raise ValueError('{} recipe doesn\'t define a dir_name, but '
                             'this is necessary'.format(self.name))
        return join(self.get_build_container_dir(arch), self.dir_name)

    def get_jni_dir(self):
        return join(self.ctx.bootstrap.build_dir, 'jni')


class NDKRecipe(Recipe):
    '''A recipe class for any NDK project not included in the bootstrap.'''

    generated_libraries = []

    def should_build(self, arch):
        lib_dir = self.get_lib_dir(arch)

        for lib in self.generated_libraries:
            if not exists(join(lib_dir, lib)):
                return True

        return False

    def get_lib_dir(self, arch):
        return join(self.get_build_dir(arch.arch), 'obj', 'local', arch.arch)

    def get_jni_dir(self, arch):
        return join(self.get_build_dir(arch.arch), 'jni')

    def build_arch(self, arch, *extra_args):
        super(NDKRecipe, self).build_arch(arch)

        env = self.get_recipe_env(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            shprint(sh.ndk_build, 'V=1', 'APP_ABI=' + arch.arch, *extra_args, _env=env)


class PythonRecipe(Recipe):
    site_packages_name = None
    '''The name of the module's folder when installed in the Python
    site-packages (e.g. for pyjnius it is 'jnius')'''

    call_hostpython_via_targetpython = True
    '''If True, tries to install the module using the hostpython binary
    copied to the target (normally arm) python build dir. However, this
    will fail if the module tries to import e.g. _io.so. Set this to False
    to call hostpython from its own build dir, installing the module in
    the right place via arguments to setup.py. However, this may not set
    the environment correctly and so False is not the default.'''

    install_in_hostpython = False
    '''If True, additionally installs the module in the hostpython build
    dir. This will make it available to other recipes if
    call_hostpython_via_targetpython is False.
    '''

    setup_extra_args = []
    '''List of extra arugments to pass to setup.py'''

    @property
    def hostpython_location(self):
        if not self.call_hostpython_via_targetpython:
            return join(
                Recipe.get_recipe('hostpython2', self.ctx).get_build_dir(),
                'hostpython')
        return self.ctx.hostpython

    def should_build(self, arch):
        print('name is', self.site_packages_name, type(self))
        name = self.site_packages_name
        if name is None:
            name = self.name
        if self.ctx.has_package(name):
            info('Python package already exists in site-packages')
            return False
        info('{} apparently isn\'t already in site-packages'.format(name))
        return True

    def build_arch(self, arch):
        '''Install the Python module by calling setup.py install with
        the target Python dir.'''
        super(PythonRecipe, self).build_arch(arch)
        self.install_python_package(arch)

    def install_python_package(self, arch, name=None, env=None, is_dir=True):
        '''Automate the installation of a Python package (or a cython
        package where the cython components are pre-built).'''
        # arch = self.filtered_archs[0]  # old kivy-ios way
        if name is None:
            name = self.name
        if env is None:
            env = self.get_recipe_env(arch)

        info('Installing {} into site-packages'.format(self.name))

        with current_directory(self.get_build_dir(arch.arch)):
            # hostpython = sh.Command(self.ctx.hostpython)
            hostpython = sh.Command(self.hostpython_location)

            if self.call_hostpython_via_targetpython:
                shprint(hostpython, 'setup.py', 'install', '-O2', _env=env,
                        *self.setup_extra_args)
            else:
                hppath = join(dirname(self.hostpython_location), 'Lib',
                              'site-packages')
                hpenv = env.copy()
                if 'PYTHONPATH' in hpenv:
                    hpenv['PYTHONPATH'] = ':'.join([hppath] +
                                                   hpenv['PYTHONPATH'].split(':'))
                else:
                    hpenv['PYTHONPATH'] = hppath
                shprint(hostpython, 'setup.py', 'install', '-O2',
                        '--root={}'.format(self.ctx.get_python_install_dir()),
                        '--install-lib=lib/python2.7/site-packages',
                        _env=hpenv, *self.setup_extra_args)
                # AND: Hardcoded python2.7 needs fixing

            # If asked, also install in the hostpython build dir
            if self.install_in_hostpython:
                shprint(hostpython, 'setup.py', 'install', '-O2',
                        '--root={}'.format(dirname(self.hostpython_location)),
                        '--install-lib=Lib/site-packages',
                        _env=env, *self.setup_extra_args)


class CompiledComponentsPythonRecipe(PythonRecipe):
    pre_build_ext = False

    build_cmd = 'build_ext'

    def build_arch(self, arch):
        '''Build any cython components, then install the Python module by
        calling setup.py install with the target Python dir.
        '''
        Recipe.build_arch(self, arch)
        self.build_compiled_components(arch)
        self.install_python_package(arch)

    def build_compiled_components(self, arch):
        info('Building compiled components in {}'.format(self.name))

        env = self.get_recipe_env(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            hostpython = sh.Command(self.hostpython_location)
            if self.call_hostpython_via_targetpython:
                shprint(hostpython, 'setup.py', self.build_cmd, '-v',
                        _env=env, *self.setup_extra_args)
            else:
                hppath = join(dirname(self.hostpython_location), 'Lib',
                              'site-packages')
                if 'PYTHONPATH' in env:
                    env['PYTHONPATH'] = hppath + ':' + env['PYTHONPATH']
                else:
                    env['PYTHONPATH'] = hppath
                shprint(hostpython, 'setup.py', self.build_cmd, '-v', _env=env,
                        *self.setup_extra_args)
            build_dir = glob.glob('build/lib.*')[0]
            shprint(sh.find, build_dir, '-name', '"*.o"', '-exec',
                    env['STRIP'], '{}', ';', _env=env)


class CythonRecipe(PythonRecipe):
    pre_build_ext = False
    cythonize = True

    def build_arch(self, arch):
        '''Build any cython components, then install the Python module by
        calling setup.py install with the target Python dir.
        '''
        Recipe.build_arch(self, arch)
        self.build_cython_components(arch)
        self.install_python_package(arch)

    def build_cython_components(self, arch):
        info('Cythonizing anything necessary in {}'.format(self.name))
        env = self.get_recipe_env(arch)
        with current_directory(self.get_build_dir(arch.arch)):
            hostpython = sh.Command(self.ctx.hostpython)
            info('Trying first build of {} to get cython files: this is '
                 'expected to fail'.format(self.name))
            try:
                shprint(hostpython, 'setup.py', 'build_ext', _env=env,
                        *self.setup_extra_args)
            except sh.ErrorReturnCode_1:
                print()
                info('{} first build failed (as expected)'.format(self.name))

            info('Running cython where appropriate')
            shprint(sh.find, self.get_build_dir(arch.arch), '-iname', '*.pyx',
                    '-exec', self.ctx.cython, '{}', ';', _env=env)
            info('ran cython')

            shprint(hostpython, 'setup.py', 'build_ext', '-v', _env=env,
                    _tail=20, _critical=True, *self.setup_extra_args)

            print('stripping')
            build_lib = glob.glob('./build/lib*')
            shprint(sh.find, build_lib[0], '-name', '*.o', '-exec',
                    env['STRIP'], '{}', ';', _env=env)
            print('stripped!?')
            # exit(1)

    # def cythonize_file(self, filename):
    #     if filename.startswith(self.build_dir):
    #         filename = filename[len(self.build_dir) + 1:]
    #     print("Cythonize {}".format(filename))
    #     cmd = sh.Command(join(self.ctx.root_dir, "tools", "cythonize.py"))
    #     shprint(cmd, filename)

    # def cythonize_build(self):
    #     if not self.cythonize:
    #         return
    #     root_dir = self.build_dir
    #     for root, dirnames, filenames in walk(root_dir):
    #         for filename in fnmatch.filter(filenames, "*.pyx"):
    #             self.cythonize_file(join(root, filename))

    def get_recipe_env(self, arch):
        env = super(CythonRecipe, self).get_recipe_env(arch)
        env['LDFLAGS'] = env['LDFLAGS'] + ' -L{}'.format(
            self.ctx.get_libs_dir(arch.arch) +
            '-L{}'.format(self.ctx.libs_dir))
        env['LDSHARED'] = join(self.ctx.root_dir, 'tools', 'liblink')
        env['LIBLINK'] = 'NOTNONE'
        env['NDKPLATFORM'] = self.ctx.ndk_platform

        # Every recipe uses its own liblink path, object files are
        # collected and biglinked later
        liblink_path = join(self.get_build_container_dir(arch.arch),
                            'objects_{}'.format(self.name))
        env['LIBLINK_PATH'] = liblink_path
        ensure_dir(liblink_path)
        return env
