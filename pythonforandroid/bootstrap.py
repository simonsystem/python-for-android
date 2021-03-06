from os.path import (join, dirname, isdir, splitext, basename)
from os import listdir
import sh
import glob
import json
import importlib

from pythonforandroid.logger import (warning, shprint, info, logger,
                                     debug)
from pythonforandroid.util import (current_directory, ensure_dir,
                                   temp_directory, which)
from pythonforandroid.recipe import Recipe


class Bootstrap(object):
    '''An Android project template, containing recipe stuff for
    compilation and templated fields for APK info.
    '''
    name = ''
    jni_subdir = '/jni'
    ctx = None

    bootstrap_dir = None

    build_dir = None
    dist_dir = None
    dist_name = None
    distribution = None

    recipe_depends = []

    can_be_chosen_automatically = True
    '''Determines whether the bootstrap can be chosen as one that
    satisfies user requirements. If False, it will not be returned
    from Bootstrap.get_bootstrap_from_recipes.
    '''

    # Other things a Bootstrap might need to track (maybe separately):
    # ndk_main.c
    # whitelist.txt
    # blacklist.txt

    @property
    def dist_dir(self):
        '''The dist dir at which to place the finished distribution.'''
        if self.distribution is None:
            warning('Tried to access {}.dist_dir, but {}.distribution '
                    'is None'.format(self, self))
            exit(1)
        return self.distribution.dist_dir

    @property
    def jni_dir(self):
        return self.name + self.jni_subdir

    def get_build_dir(self):
        return join(self.ctx.build_dir, 'bootstrap_builds', self.name)

    def get_dist_dir(self, name):
        return join(self.ctx.dist_dir, name)

    @property
    def name(self):
        modname = self.__class__.__module__
        return modname.split(".", 2)[-1]

    def prepare_build_dir(self):
        '''Ensure that a build dir exists for the recipe. This same single
        dir will be used for building all different archs.'''
        self.build_dir = self.get_build_dir()
        shprint(sh.cp, '-r',
                join(self.bootstrap_dir, 'build'),
                # join(self.ctx.root_dir,
                #      'bootstrap_templates',
                #      self.name),
                self.build_dir)
        with current_directory(self.build_dir):
            with open('project.properties', 'w') as fileh:
                fileh.write('target=android-{}'.format(self.ctx.android_api))

    def prepare_dist_dir(self, name):
        # self.dist_dir = self.get_dist_dir(name)
        ensure_dir(self.dist_dir)

    def run_distribute(self):
        # print('Default bootstrap being used doesn\'t know how '
        #       'to distribute...failing.')
        # exit(1)
        with current_directory(self.dist_dir):
            info('Saving distribution info')
            with open('dist_info.json', 'w') as fileh:
                json.dump({'dist_name': self.ctx.dist_name,
                           'bootstrap': self.ctx.bootstrap.name,
                           'archs': [arch.arch for arch in self.ctx.archs],
                           'recipes': self.ctx.recipe_build_order},
                          fileh)

    @classmethod
    def list_bootstraps(cls):
        '''Find all the available bootstraps and return them.'''
        forbidden_dirs = ('__pycache__', )
        bootstraps_dir = join(dirname(__file__), 'bootstraps')
        for name in listdir(bootstraps_dir):
            if name in forbidden_dirs:
                continue
            filen = join(bootstraps_dir, name)
            if isdir(filen):
                yield name

    @classmethod
    def get_bootstrap_from_recipes(cls, recipes, ctx):
        '''Returns a bootstrap whose recipe requirements do not conflict with
        the given recipes.'''
        info('Trying to find a bootstrap that matches the given recipes.')
        bootstraps = [cls.get_bootstrap(name, ctx)
                      for name in cls.list_bootstraps()]
        acceptable_bootstraps = []
        for bs in bootstraps:
            ok = True
            if not bs.can_be_chosen_automatically:
                ok = False
            for recipe in bs.recipe_depends:
                recipe = Recipe.get_recipe(recipe, ctx)
                if any([conflict in recipes for conflict in recipe.conflicts]):
                    ok = False
                    break
            for recipe in recipes:
                recipe = Recipe.get_recipe(recipe, ctx)
                if any([conflict in bs.recipe_depends
                        for conflict in recipe.conflicts]):
                    ok = False
                    break
            if ok:
                acceptable_bootstraps.append(bs)
        info('Found {} acceptable bootstraps: {}'.format(
            len(acceptable_bootstraps),
            [bs.name for bs in acceptable_bootstraps]))
        if acceptable_bootstraps:
            info('Using the first of these: {}'
                 .format(acceptable_bootstraps[0].name))
            return acceptable_bootstraps[0]
        return None

    @classmethod
    def get_bootstrap(cls, name, ctx):
        '''Returns an instance of a bootstrap with the given name.

        This is the only way you should access a bootstrap class, as
        it sets the bootstrap directory correctly.
        '''
        # AND: This method will need to check user dirs, and access
        # bootstraps in a slightly different way
        if name is None:
            return None
        if not hasattr(cls, 'bootstraps'):
            cls.bootstraps = {}
        if name in cls.bootstraps:
            return cls.bootstraps[name]
        mod = importlib.import_module('pythonforandroid.bootstraps.{}'
                                      .format(name))
        if len(logger.handlers) > 1:
            logger.removeHandler(logger.handlers[1])
        bootstrap = mod.bootstrap
        bootstrap.bootstrap_dir = join(ctx.root_dir, 'bootstraps', name)
        bootstrap.ctx = ctx
        return bootstrap

    def distribute_libs(self, arch, src_dirs, wildcard='*'):
        '''Copy existing arch libs from build dirs to current dist dir.'''
        info('Copying libs')
        tgt_dir = join('libs', arch.arch)
        ensure_dir(tgt_dir)
        for src_dir in src_dirs:
            for lib in glob.glob(join(src_dir, wildcard)):
                shprint(sh.cp, '-a', lib, tgt_dir)

    def distribute_javaclasses(self, javaclass_dir):
        '''Copy existing javaclasses from build dir to current dist dir.'''
        info('Copying java files')
        for filename in glob.glob(javaclass_dir):
            shprint(sh.cp, '-a', filename, 'src')

    def distribute_aars(self, arch):
        '''Process existing .aar bundles and copy to current dist dir.'''
        info('Unpacking aars')
        for aar in glob.glob(join(self.ctx.aars_dir, '*.aar')):
            self._unpack_aar(aar, arch)

    def _unpack_aar(self, aar, arch):
        '''Unpack content of .aar bundle and copy to current dist dir.'''
        with temp_directory() as temp_dir:
            name = splitext(basename(aar))[0]
            jar_name = name + '.jar'
            info("unpack {} aar".format(name))
            debug("  from {}".format(aar))
            debug("  to {}".format(temp_dir))
            shprint(sh.unzip, '-o', aar, '-d', temp_dir)

            jar_src = join(temp_dir, 'classes.jar')
            jar_tgt = join('libs', jar_name)
            debug("copy {} jar".format(name))
            debug("  from {}".format(jar_src))
            debug("  to {}".format(jar_tgt))
            ensure_dir('libs')
            shprint(sh.cp, '-a', jar_src, jar_tgt)

            so_src_dir = join(temp_dir, 'jni', arch.arch)
            so_tgt_dir = join('libs', arch.arch)
            debug("copy {} .so".format(name))
            debug("  from {}".format(so_src_dir))
            debug("  to {}".format(so_tgt_dir))
            ensure_dir(so_tgt_dir)
            so_files = glob.glob(join(so_src_dir, '*.so'))
            for f in so_files:
                shprint(sh.cp, '-a', f, so_tgt_dir)

    def strip_libraries(self, arch):
        info('Stripping libraries')
        env = arch.get_env()
        strip = which('arm-linux-androideabi-strip', env['PATH'])
        if strip is None:
            warning('Can\'t find strip in PATH...')
            return
        strip = sh.Command(strip)
        filens = shprint(sh.find, join(self.dist_dir, 'private'),
                         join(self.dist_dir, 'libs'),
                         '-iname', '*.so', _env=env).stdout.decode('utf-8')
        logger.info('Stripping libraries in private dir')
        for filen in filens.split('\n'):
            try:
                strip(filen, _env=env)
            except sh.ErrorReturnCode_1:
                logger.debug('Failed to strip ' + filen)
