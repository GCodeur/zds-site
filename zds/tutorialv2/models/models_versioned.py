# coding: utf-8

import json as json_writer
from git import Repo
import os
import shutil

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext_lazy as _
from zds.settings import ZDS_APP
from zds.tutorialv2.utils import default_slug_pool, export_content, get_commit_author, InvalidOperationError
from zds.utils import slugify
from zds.utils.misc import compute_hash
from zds.utils.tutorials import get_blob


class Container:
    """
    A container, which can have sub-Containers or Extracts.

    A Container has a title, a introduction and a conclusion, a parent (which can be None) and a position into this
    parent (which is 1 by default).

    It has also a tree depth.

    A container could be either a tutorial/article, a part or a chapter.
    """

    title = ''
    slug = ''
    introduction = None
    conclusion = None
    parent = None
    position_in_parent = 1
    children = []
    children_dict = {}
    slug_pool = {}

    # TODO: thumbnails ?

    def __init__(self, title, slug='', parent=None, position_in_parent=1):
        self.title = title
        self.slug = slug
        self.parent = parent
        self.position_in_parent = position_in_parent

        self.children = []  # even if you want, do NOT remove this line
        self.children_dict = {}

        self.slug_pool = default_slug_pool()

    def __unicode__(self):
        return u'<Conteneur \'{}\'>'.format(self.title)

    def has_extracts(self):
        """Note : this function rely on the fact that the children can only be of one type.

        :return: `True` if the container has extract as children, `False` otherwise.
        """
        if len(self.children) == 0:
            return False
        return isinstance(self.children[0], Extract)

    def has_sub_containers(self):
        """Note : this function rely on the fact that the children can only be of one type.

        :return: `True` if the container has containers as children, `False` otherwise.
        """
        if len(self.children) == 0:
            return False
        return isinstance(self.children[0], Container)

    def get_last_child_position(self):
        """
        :return: the position of the last child
        """
        return len(self.children)

    def get_tree_depth(self):
        """Represent the depth where this container is found
        Tree depth is no more than 2, because there is 3 levels for Containers :
        - PublishableContent (0),
        - Part (1),
        - Chapter (2)
        .. attention::
            that `'max_tree_depth` is `2` to ensure that there is no more than 3 levels

        :return: Tree depth
        """
        depth = 0
        current = self
        while current.parent is not None:
            current = current.parent
            depth += 1
        return depth

    def get_tree_level(self):
        """Represent the level in the tree of this container, i.e the depth of its deepest child

        :return: tree level
        """

        if len(self.children) == 0:
            return 1
        elif isinstance(self.children[0], Extract):
            return 2
        else:
            return 1 + max([i.get_tree_level() for i in self.children])

    def has_child_with_path(self, child_path):
        """Check that the given path represent the full path
        of a child of this container.

        :param child_path: the full path (/maincontainer/subc1/subc2/childslug) we want to check
        """
        if self.get_path(True) not in child_path:
            return False
        return child_path.replace(self.get_path(True), "").replace("/", "") in self.children_dict

    def top_container(self):
        """
        :return: Top container (for which parent is `None`)
        """
        current = self
        while current.parent is not None:
            current = current.parent
        return current

    def get_unique_slug(self, title):
        """Generate a slug from title, and check if it is already in slug pool. If it is the case, recursively add a
        "-x" to the end, where "x" is a number starting from 1. When generated, it is added to the slug pool.

        :param title: title from which the slug is generated (with `slugify()`)
        :return: the unique slug
        """
        base = slugify(title)
        try:
            n = self.slug_pool[base]
        except KeyError:
            new_slug = base
            self.slug_pool[base] = 0
        else:
            new_slug = base + '-' + str(n)
        self.slug_pool[base] += 1
        self.slug_pool[new_slug] = 1
        return new_slug

    def add_slug_to_pool(self, slug):
        """Add a slug to the slug pool to be taken into account when generate a unique slug

        :param slug: the slug to add
        """
        try:
            self.slug_pool[slug]  # test access
        except KeyError:
            self.slug_pool[slug] = 1
        else:
            raise Exception('slug "{}" already in the slug pool !'.format(slug))

    def long_slug(self):
        """
        :return: a long slug that embed slugs of parents
        """
        long_slug = ''
        if self.parent:
            long_slug = self.parent.long_slug() + '__'
        return long_slug + self.slug

    def can_add_container(self):
        """
        :return: True if this container accept child container, false otherwise
        """
        if not self.has_extracts():
            if self.get_tree_depth() < ZDS_APP['content']['max_tree_depth'] - 1:
                if not self.top_container().is_article:
                    return True
        return False

    def can_add_extract(self):
        """
        :return: True if this container accept child extract, false otherwise
        """
        if not self.has_sub_containers():
            if self.get_tree_depth() <= ZDS_APP['content']['max_tree_depth']:
                return True
        return False

    def add_container(self, container, generate_slug=False):
        """Add a child Container, but only if no extract were previously added and tree depth is < 2.
        .. attention::
            this function will also raise an Exception if article, because it cannot contain child container

        :param container: the new container
        :param generate_slug: if `True`, ask the top container an unique slug for this object
        """
        if self.can_add_container():
            if generate_slug:
                container.slug = self.get_unique_slug(container.title)
            else:
                self.add_slug_to_pool(container.slug)
            container.parent = self
            container.position_in_parent = self.get_last_child_position() + 1
            self.children.append(container)
            self.children_dict[container.slug] = container
        else:
            raise InvalidOperationError("Cannot add another level to this container")

    def add_extract(self, extract, generate_slug=False):
        """Add a child container, but only if no container were previously added

        :param extract: the new extract
        :param generate_slug: if `True`, ask the top container an unique slug for this object
        """
        if self.can_add_extract():
            if generate_slug:
                extract.slug = self.get_unique_slug(extract.title)
            else:
                self.add_slug_to_pool(extract.slug)
            extract.container = self
            extract.position_in_parent = self.get_last_child_position() + 1
            self.children.append(extract)
            self.children_dict[extract.slug] = extract
            extract.text = extract.get_path(True)
        else:
            raise InvalidOperationError("Can't add an extract if this container already contains containers.")

    def update_children(self):
        """Update the path for introduction and conclusion for the container and all its children. If the children is an
        extract, update the path to the text instead. This function is useful when `self.slug` has
        changed.
        Note : this function does not account for a different arrangement of the files.
        """
        # TODO : path comparison instead of pure rewritring ?
        self.introduction = os.path.join(self.get_path(relative=True), "introduction.md")
        self.conclusion = os.path.join(self.get_path(relative=True), "conclusion.md")
        for child in self.children:
            if isinstance(child, Container):
                child.update_children()
            elif isinstance(child, Extract):
                child.text = child.get_path(relative=True)
        # TODO : does this function should also rewrite `slug_pool` ?

    def get_path(self, relative=False):
        """Get the physical path to the draft version of the container.
        Note: this function rely on the fact that the top container is VersionedContainer.

        :param relative: if `True`, the path will be relative, absolute otherwise.
        :return: physical path
        """
        base = ''
        if self.parent:
            base = self.parent.get_path(relative=relative)
        return os.path.join(base, self.slug)

    def get_prod_path(self, relative=False):
        """Get the physical path to the public version of the container. If the container have extracts, then it
        returns the final HTML file.

        :param relative: return a relative path instead of an absolute one
        :return: physical path
        """
        base = ''
        if self.parent:
            base = self.parent.get_prod_path(relative=relative)
        path = os.path.join(base, self.slug)

        if self.has_extracts():
            path += '.html'

        return path

    def get_absolute_url(self):
        """
        :return: url to access the container
        """
        return self.top_container().get_absolute_url() + self.get_path(relative=True) + '/'

    def get_absolute_url_online(self):
        base = ''

        if self.parent:
            base = self.parent.get_absolute_url_online()

        base += self.slug + '/'

        return base

    def get_absolute_url_beta(self):
        """
        :return: url to access the container in beta
        """

        if self.top_container().sha_beta is not None:
            base = ''
            if self.parent:
                base = self.parent.get_absolute_url_beta()

            return base + self.slug + '/'
        else:
            return self.get_absolute_url()

    def get_edit_url(self):
        """
        :return: url to edit the container
        """
        slugs = [self.slug]
        parent = self.parent
        while parent is not None:
            slugs.append(parent.slug)
            parent = parent.parent
        slugs.reverse()
        args = [self.top_container().pk]
        args.extend(slugs)

        return reverse('content:edit-container', args=args)

    def get_delete_url(self):
        """
        :return: url to edit the container
        """
        slugs = [self.slug]
        parent = self.parent
        while parent is not None:
            slugs.append(parent.slug)
            parent = parent.parent
        slugs.reverse()
        args = [self.top_container().pk]
        args.extend(slugs)

        return reverse('content:delete', args=args)

    def get_introduction(self):
        """
        :return: the introduction from the file in `self.introduction`
        """
        if self.introduction:
            return get_blob(self.top_container().repository.commit(self.top_container().current_version).tree,
                            self.introduction)

    def get_conclusion(self):
        """
        :return: the conclusion from the file in `self.conclusion`
        """
        if self.conclusion:
            return get_blob(self.top_container().repository.commit(self.top_container().current_version).tree,
                            self.conclusion)

    def get_introduction_online(self):
        if self.introduction:
            path = os.path.join(self.top_container().get_prod_path(), self.introduction)
            if os.path.isfile(path):
                return open(path, 'r').read()

    def get_conclusion_online(self):
        if self.conclusion:
            path = os.path.join(self.top_container().get_prod_path(), self.conclusion)
            if os.path.isfile(path):
                return open(path, 'r').read()

    def get_content_online(self):
        if os.path.isfile(self.get_prod_path()):
            return open(self.get_prod_path(), 'r').read()

    def compute_hash(self):
        """Compute an MD5 hash from the introduction and conclusion, for comparison purpose

        :return: MD5 hash
        """

        files = []
        if self.introduction:
            files.append(os.path.join(self.top_container().get_path(), self.introduction))
        if self.conclusion:
            files.append(os.path.join(self.top_container().get_path(), self.conclusion))

        return compute_hash(files)

    def repo_update(self, title, introduction, conclusion, commit_message='', do_commit=True):
        """Update the container information and commit them into the repository

        :param title: the new title
        :param introduction: the new introduction text
        :param conclusion: the new conclusion text
        :param commit_message: commit message that will be used instead of the default one
        :param do_commit: perform the commit in repository if `True`
        :return : commit sha
        """

        if title is None:
            raise PermissionDenied

        repo = self.top_container().repository

        # update title
        if title != self.title:
            self.title = title
            if self.get_tree_depth() > 0:  # if top container, slug is generated from DB, so already changed
                old_path = self.get_path(relative=True)
                old_slug = self.slug

                # move things
                self.slug = self.parent.get_unique_slug(title)
                new_path = self.get_path(relative=True)
                repo.index.move([old_path, new_path])

                # update manifest
                self.update_children()

                # update parent children dict:
                self.parent.children_dict.pop(old_slug)
                self.parent.children_dict[self.slug] = self

        # update introduction and conclusion (if any)
        path = self.top_container().get_path()
        rel_path = self.get_path(relative=True)

        if introduction is not None:
            if self.introduction is None:
                self.introduction = os.path.join(rel_path, 'introduction.md')

            f = open(os.path.join(path, self.introduction), "w")
            f.write(introduction.encode('utf-8'))
            f.close()
            repo.index.add([self.introduction])

        elif self.introduction:
            repo.index.remove([self.introduction])
            os.remove(os.path.join(path, self.introduction))
            self.introduction = None

        if conclusion is not None:
            if self.conclusion is None:
                self.conclusion = os.path.join(rel_path, 'conclusion.md')

            f = open(os.path.join(path, self.conclusion), "w")
            f.write(conclusion.encode('utf-8'))
            f.close()
            repo.index.add([self.conclusion])

        elif self.conclusion:
            repo.index.remove([self.conclusion])
            os.remove(os.path.join(path, self.conclusion))
            self.conclusion = None

        self.top_container().dump_json()
        repo.index.add(['manifest.json'])

        if commit_message == '':
            commit_message = _(u'Mise à jour de « {} »').format(self.title)

        if do_commit:
            return self.top_container().commit_changes(commit_message)

    def repo_add_container(self, title, introduction, conclusion, commit_message='', do_commit=True):
        """
        :param title: title of the new container
        :param introduction: text of its introduction
        :param conclusion: text of its conclusion
        :param commit_message: commit message that will be used instead of the default one
        :param do_commit: perform the commit in repository if `True`
        :return: commit sha
        """
        subcontainer = Container(title)

        # can a subcontainer be added ?
        try:
            self.add_container(subcontainer, generate_slug=True)
        except Exception:
            raise PermissionDenied

        # create directory
        repo = self.top_container().repository
        path = self.top_container().get_path()
        rel_path = subcontainer.get_path(relative=True)
        os.makedirs(os.path.join(path, rel_path), mode=0o777)

        repo.index.add([rel_path])

        # make it
        if commit_message == '':
            commit_message = _(u'Création du conteneur « {} »').format(title)

        return subcontainer.repo_update(
            title, introduction, conclusion, commit_message=commit_message, do_commit=do_commit)

    def repo_add_extract(self, title, text, commit_message='', do_commit=True):
        """
        :param title: title of the new extract
        :param text: text of the new extract
        :param commit_message: commit message that will be used instead of the default one
        :param do_commit: perform the commit in repository if `True`
        :return: commit sha
        """
        extract = Extract(title)

        # can an extract be added ?
        try:
            self.add_extract(extract, generate_slug=True)
        except Exception:
            raise PermissionDenied

        # make it
        if commit_message == '':
            commit_message = _(u'Création de l\'extrait « {} »').format(title)

        return extract.repo_update(title, text, commit_message=commit_message, do_commit=do_commit)

    def repo_delete(self, commit_message='', do_commit=True):
        """
        :param commit_message: commit message used instead of default one if provided
        :param do_commit: tells if we have to commit the change now or let the outter program do it
        :return: commit sha
        """
        path = self.get_path(relative=True)
        repo = self.top_container().repository
        repo.index.remove([path], r=True)
        shutil.rmtree(self.get_path())  # looks like removing from git is not enough !!

        # now, remove from manifest
        # work only if slug is correct
        top = self.parent
        top.children_dict.pop(self.slug)
        top.children.pop(top.children.index(self))

        # commit
        top.top_container().dump_json()
        repo.index.add(['manifest.json'])

        if commit_message == '':
            commit_message = _(u'Suppression du conteneur « {} »').format(self.title)

        if do_commit:
            return self.top_container().commit_changes(commit_message)

    def move_child_up(self, child_slug):
        """Change the child's ordering by moving up the child whose slug equals child_slug.
        This method **does not** automaticaly update the repo

        :param child_slug: the child's slug
        :raise ValueError: if the slug does not refer to an existing child
        :raise IndexError: if the extract is already the first child
        """
        if child_slug not in self.children_dict:
            raise ValueError(child_slug + " does not exist")
        child_pos = self.children.index(self.children_dict[child_slug])
        if child_pos == 0:
            raise IndexError(child_slug + " is the first element")
        self.children[child_pos], self.children[child_pos - 1] = self.children[child_pos - 1], self.children[child_pos]
        self.children[child_pos].position_in_parent = child_pos + 1
        self.children[child_pos - 1].position_in_parent = child_pos

    def move_child_down(self, child_slug):
        """Change the child's ordering by moving down the child whose slug equals child_slug.
        This method **does not** automaticaly update the repo

        :param child_slug: the child's slug
        :raise ValueError: if the slug does not refer to an existing child
        :raise IndexError: if the extract is already the last child
        """
        if child_slug not in self.children_dict:
            raise ValueError(child_slug + " does not exist")
        child_pos = self.children.index(self.children_dict[child_slug])
        if child_pos == len(self.children) - 1:
            raise IndexError(child_slug + " is the last element")
        self.children[child_pos], self.children[child_pos + 1] = self.children[child_pos + 1], self.children[child_pos]
        self.children[child_pos].position_in_parent = child_pos
        self.children[child_pos + 1].position_in_parent = child_pos + 1

    def move_child_after(self, child_slug, refer_slug):
        """Change the child's ordering by moving the child to be below the reference child.
        This method **does not** automaticaly update the repo

        :param child_slug: the child's slug
        :param refer_slug: the referent child's slug.
        :raise ValueError: if one slug does not refer to an existing child
        """
        if child_slug not in self.children_dict:
            raise ValueError(child_slug + " does not exist")
        if refer_slug not in self.children_dict:
            raise ValueError(refer_slug + " does not exist")
        child_pos = self.children.index(self.children_dict[child_slug])
        refer_pos = self.children.index(self.children_dict[refer_slug])

        # if we want our child to get down (reference is an lower child)
        if child_pos < refer_pos:
            for i in range(child_pos, refer_pos):
                self.move_child_down(child_slug)
        elif child_pos > refer_pos:
            # if we want our child to get up (reference is an upper child)
            for i in range(child_pos, refer_pos + 1, - 1):
                self.move_child_up(child_slug)

    def move_child_before(self, child_slug, refer_slug):
        """Change the child's ordering by moving the child to be just above the reference child. .
        This method **does not** automaticaly update the repo

        :param child_slug: the child's slug
        :param refer_slug: the referent child's slug.
        :raise ValueError: if one slug does not refer to an existing child
        """
        if child_slug not in self.children_dict:
            raise ValueError(child_slug + " does not exist")
        if refer_slug not in self.children_dict:
            raise ValueError(refer_slug + " does not exist")
        child_pos = self.children.index(self.children_dict[child_slug])
        refer_pos = self.children.index(self.children_dict[refer_slug])

        # if we want our child to get down (reference is an lower child)
        if child_pos < refer_pos:
            for i in range(child_pos, refer_pos - 1):
                self.move_child_down(child_slug)
        elif child_pos > refer_pos:
            # if we want our child to get up (reference is an upper child)
            for i in range(child_pos, refer_pos, - 1):
                self.move_child_up(child_slug)

    def traverse(self, only_container=True):
        """Traverse the container

        :param only_container: if we only want container's paths, not extract
        :return: a generator that traverse all the container recursively (depth traversal)
        """
        yield self
        for child in self.children:
            if isinstance(child, Container):
                for _y in child.traverse(only_container):
                    yield _y
            elif not only_container:
                yield child

    def get_level_as_string(self):
        """
        :return: The string representation of this level
        """
        if self.get_tree_depth() == 0:
            return self.type
        elif self.get_tree_depth() == 1:
            return _(u"Partie")
        elif self.get_tree_depth() == 2:
            return _(u"Chapitre")
        return _(u"Sous-chapitre")

    def get_next_level_as_string(self):
        if self.get_tree_depth() == 0 and self.can_add_container():
            return _(u"Partie")
        elif self.get_tree_depth() == 1 and self.can_add_container():
            return _(u"Chapitre")
        else:
            return _(u"Section")


class Extract:
    """
    A content extract from a Container.

    It has a title, a position in the parent container and a text.
    """

    title = ''
    slug = ''
    container = None
    position_in_parent = 1
    text = None

    def __init__(self, title, slug='', container=None, position_in_parent=1):
        self.title = title
        self.slug = slug
        self.container = container
        self.position_in_parent = position_in_parent

    def __unicode__(self):
        return u'<Extrait \'{}\'>'.format(self.title)

    def get_absolute_url(self):
        """
        :return: the url to access the tutorial offline
        """
        return '{0}#{1}-{2}'.format(
            self.container.get_absolute_url(),
            self.position_in_parent,
            self.slug
        )

    def get_absolute_url_online(self):
        """
        :return: the url to access the tutorial when online
        """
        return '{0}#{1}-{2}'.format(
            self.container.get_absolute_url_online(),
            self.position_in_parent,
            self.slug
        )

    def get_absolute_url_beta(self):
        """
        :return: the url to access the tutorial when in beta
        """
        return '{0}#{1}-{2}'.format(
            self.container.get_absolute_url_beta(),
            self.position_in_parent,
            self.slug
        )

    def get_edit_url(self):
        """
        :return: url to edit the extract
        """
        slugs = [self.slug]
        parent = self.container
        while parent is not None:
            slugs.append(parent.slug)
            parent = parent.parent
        slugs.reverse()
        args = [self.container.top_container().pk]
        args.extend(slugs)

        return reverse('content:edit-extract', args=args)

    def get_full_slug(self):
        """
        get the slug of curent extract with its full path (part1/chapter1/slug_of_extract)
        this method is an alias to extract.get_path(True)[:-3] (remove .md extension)
        """
        return self.get_path(True)[:-3]

    def get_first_level_slug(self):
        """
        :return: the first_level_slug, if (and only) the parent container is a chapter
        """

        if self.container.get_tree_depth() == 2:
            return self.container.parent.slug

        return ''

    def get_delete_url(self):
        """
        :return: url to delete the extract
        """
        slugs = [self.slug]
        parent = self.container
        while parent is not None:
            slugs.append(parent.slug)
            parent = parent.parent
        slugs.reverse()
        args = [self.container.top_container().pk]
        args.extend(slugs)

        return reverse('content:delete', args=args)

    def get_path(self, relative=False):
        """
        Get the physical path to the draft version of the extract.
        :param relative: if `True`, the path will be relative, absolute otherwise.
        :return: physical path
        """
        return os.path.join(self.container.get_path(relative=relative), self.slug) + '.md'

    def get_text(self):
        """
        :return: versioned text
        """
        if self.text:
            return get_blob(
                self.container.top_container().repository.commit(self.container.top_container().current_version).tree,
                self.text)

    def compute_hash(self):
        """Compute an MD5 hash from the text, for comparison purpose

        :return: MD5 hash of the text
        """

        if self.text:
            return compute_hash([self.get_path()])

        else:
            return compute_hash([])

    def repo_update(self, title, text, commit_message='', do_commit=True):
        """
        :param title: new title of the extract
        :param text: new text of the extract
        :param commit_message: commit message that will be used instead of the default one
        :return: commit sha
        """

        if title is None:
            raise PermissionDenied

        repo = self.container.top_container().repository

        if title != self.title:
            # get a new slug
            old_path = self.get_path(relative=True)
            old_slug = self.slug
            self.title = title
            self.slug = self.container.get_unique_slug(title)

            # move file
            new_path = self.get_path(relative=True)
            repo.index.move([old_path, new_path])

            # update parent children dict:
            self.container.children_dict.pop(old_slug)
            self.container.children_dict[self.slug] = self

        # edit text
        path = self.container.top_container().get_path()

        if text is not None:
            self.text = self.get_path(relative=True)
            f = open(os.path.join(path, self.text), "w")
            f.write(text.encode('utf-8'))
            f.close()

            repo.index.add([self.text])

        elif self.text:
            if os.path.exists(os.path.join(path, self.text)):
                repo.index.remove([self.text])
                os.remove(os.path.join(path, self.text))

            self.text = None

        # make it
        self.container.top_container().dump_json()
        repo.index.add(['manifest.json'])

        if commit_message == '':
            commit_message = _(u'Modification de l\'extrait « {} », situé dans le conteneur « {} »')\
                .format(self.title, self.container.title)

        if do_commit:
            return self.container.top_container().commit_changes(commit_message)

    def repo_delete(self, commit_message='', do_commit=True):
        """
        :param commit_message: commit message used instead of default one if provided
        :param do_commit: tells if we have to commit the change now or let the outter program do it
        :return: commit sha, None if no commit is done
        """
        path = self.text
        repo = self.container.top_container().repository

        repo.index.remove([path])
        os.remove(self.get_path())  # looks like removing from git is not enough

        # now, remove from manifest
        # work only if slug is correct!!
        top = self.container
        top.children_dict.pop(self.slug, None)
        top.children.pop(top.children.index(self))

        # commit
        top.top_container().dump_json()
        repo.index.add(['manifest.json'])

        if commit_message == '':
            commit_message = _(u'Suppression de l\'extrait « {} »').format(self.title)

        if do_commit:
            return self.container.top_container().commit_changes(commit_message)

    def get_tree_depth(self):
        """
        Represent the depth where this exrtact is found
        Tree depth is no more than 3, because there is 3 levels for Containers +1 for extract :
        - PublishableContent (0),
        - Part (1),
        - Chapter (2)
        Note that `'max_tree_depth` is `2` to ensure that there is no more than 3 levels
        :return: Tree depth
        """
        depth = 1
        current = self.container
        while current.parent is not None:
            current = current.parent
            depth += 1
        return depth


class VersionedContent(Container):
    """
    This class is used to handle a specific version of a tutorial.tutorial

    It is created from the "manifest.json" file, and could dump information in it.

    For simplicity, it also contains DB information (but cannot modified them!), filled at the creation.
    """

    current_version = None
    slug_repository = ''
    repository = None

    PUBLIC = False  # this variable is set to true when the VersionedContent is created from the public repository

    # Metadata from json :
    description = ''
    type = ''
    licence = None

    # Metadata from DB :
    pk = 0
    sha_draft = None
    sha_beta = None
    sha_public = None
    sha_validation = None
    is_beta = False
    is_validation = False
    is_public = False
    in_beta = False
    in_validation = False
    in_public = False

    is_article = False
    is_tutorial = False

    authors = None
    subcategory = None
    image = None
    creation_date = None
    pubdate = None
    update_date = None
    source = None
    antispam = True

    def __init__(self, current_version, _type, title, slug, slug_repository=''):
        """
        :param current_version: version of the content
        :param _type: either "TUTORIAL" or "ARTICLE"
        :param title: title of the content
        :param slug: slug of the content
        :param slug_repository: slug of the directory that contains the repository, named after database slug.
            if not provided, use `self.slug` instead.
        """

        Container.__init__(self, title, slug)
        self.current_version = current_version
        self.type = _type

        if slug_repository != '':
            self.slug_repository = slug_repository
        else:
            self.slug_repository = slug

        if self.slug != '' and os.path.exists(self.get_path()):
            self.repository = Repo(self.get_path())

    def __unicode__(self):
        return self.title

    def get_absolute_url(self, version=None):
        """
        :return: the url to access the tutorial when offline
        """
        url = reverse('content:view', args=[self.pk, self.slug])

        if version and version != self.sha_draft:
            url += '?version=' + version

        return url

    def get_absolute_url_online(self):
        """
        :return: the url to access the content when online
        """
        _reversed = ''

        if self.is_article:
            _reversed = 'article'
        elif self.is_tutorial:
            _reversed = 'tutorial'
        return reverse(_reversed + ':view', kwargs={'pk': self.pk, 'slug': self.slug})

    def get_absolute_url_beta(self):
        """
        :return: the url to access the tutorial when in beta
        """
        if self.in_beta:
            return reverse('content:beta-view', args=[self.pk, self.slug])
        else:
            return self.get_absolute_url()

    def get_path(self, relative=False, use_current_slug=False):
        """Get the physical path to the draft version of the Content.

        :param relative: if `True`, the path will be relative, absolute otherwise.
        :param use_current_slug: if `True`, use `self.slug` instead of `self.slug_last_draft`
        :return: physical path
        """
        if relative:
            return ''
        else:
            slug = self.slug_repository
            if use_current_slug:
                slug = self.slug
            return os.path.join(settings.ZDS_APP['content']['repo_private_path'], slug)

    def get_prod_path(self, relative=False):
        """Get the physical path to the public version of the content. If it has extract (so, if its a mini-tutorial or
        an article), return the HTML file.

        :param relative: return the relative path instead of the absolute one
        :return: physical path
        """
        path = ''

        if not relative:
            path = os.path.join(settings.ZDS_APP['content']['repo_public_path'], self.slug)

        if self.has_extracts():
            path = os.path.join(path, self.slug + '.html')

        return path

    def get_list_of_chapters(self):
        """
        :return: a list of chpaters (Container which contains Extracts) in the reading order
        """
        continuous_list = []
        if not self.is_article:  # article cannot be paginated
            if len(self.children) != 0 and isinstance(self.children[0], Container):  # children must be Containers !
                for child in self.children:
                    if len(child.children) != 0:
                        if isinstance(child.children[0], Extract):
                            continuous_list.append(child)  # it contains Extract, this is a chapter, so paginated
                        else:  # Container is a part
                            for sub_child in child.children:
                                continuous_list.append(sub_child)  # even if empty `sub_child.childreen`, it's chapter
        return continuous_list

    def get_json(self):
        """
        :return: raw JSON file
        """
        dct = export_content(self)
        data = json_writer.dumps(dct, indent=4, ensure_ascii=False)
        return data

    def dump_json(self, path=None):
        """Write the JSON into file
        :param path: path to the file. If `None`, write in "manifest.json"
        """
        if path is None:
            man_path = os.path.join(self.get_path(), 'manifest.json')
        else:
            man_path = path
        json_data = open(man_path, "w")
        json_data.write(self.get_json().encode('utf-8'))
        json_data.close()

    def repo_update_top_container(self, title, slug, introduction, conclusion, commit_message='', do_commit=True):
        """Update the top container information and commit them into the repository.
        Note that this is slightly different from the `repo_update()` function, because slug is generated using DB

        :param title: the new title
        :param slug: the new slug, according to title (choose using DB!!)
        :param introduction: the new introduction text
        :param conclusion: the new conclusion text
        :param commit_message: commit message that will be used instead of the default one
        :param do_commit: if `True`, also commit change
        :return : commit sha
        """

        if slug != self.slug:
            # move repository
            old_path = self.get_path(use_current_slug=True)
            self.slug = slug
            new_path = self.get_path(use_current_slug=True)
            shutil.move(old_path, new_path)
            self.repository = Repo(new_path)
            self.slug_repository = slug

        return self.repo_update(title, introduction, conclusion, commit_message=commit_message, do_commit=do_commit)

    def commit_changes(self, commit_message):
        """Commit change made to the repository

        :param commit_message: The message that will appear in content history
        """
        cm = self.repository.index.commit(commit_message, **get_commit_author())

        self.sha_draft = cm.hexsha
        self.current_version = cm.hexsha

        return cm.hexsha

    def change_child_directory(self, child, adoptive_parent):
        """Move an element of this content to a new location.
        This method changes the repository index and stage every change but does **not** commit.

        :param child: the child we want to move, can be either an Extract or a Container object
        :param adptive_parent: the container where the child *will be* moved, must be a Container object
        """

        old_path = child.get_path(False)  # absolute path because we want to access the address
        if isinstance(child, Extract):
            old_parent = child.container
            old_parent.children = [c for c in old_parent.children if c.slug != child.slug]
            adoptive_parent.add_extract(child, True)
        else:
            old_parent = child.parent
            old_parent.children = [c for c in old_parent.children if c.slug != child.slug]
            adoptive_parent.add_container(child, True)
        self.repository.index.move([old_path, child.get_path(False)])
        old_parent.update_children()
        adoptive_parent.update_children()
        self.dump_json()


class PublicContent(VersionedContent):
    """This is the public version of a VersionedContent, created from public repository
    """

    def __init__(self, current_version, _type, title, slug):
        """ This initialisation function avoid the loading of the Git repository

        :param current_version: version of the content
        :param _type: either "TUTORIAL" or "ARTICLE"
        :param title: title of the content
        :param slug: slug of the content
        """

        Container.__init__(self, title, slug)
        self.current_version = current_version
        self.type = _type
        self.PUBLIC = True  # this is a public version


class NotAPublicVersion(Exception):
    """Exception raised when a given version is not a public version as it should be"""

    def __init__(self, *args, **kwargs):
        super(NotAPublicVersion, self).__init__(self, *args, **kwargs)
