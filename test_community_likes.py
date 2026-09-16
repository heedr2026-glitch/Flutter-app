import json, unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import server
from test_owner_admin import AdminConsoleTest


class CommunityLikesTest(AdminConsoleTest):
    def customer(self, path, method='GET', data=None):
        headers = {'Authorization': 'Bearer customer', 'Content-Type': 'application/json'}
        request = Request(
            f'http://127.0.0.1:{self.http.server_port}{path}',
            method=method,
            headers=headers,
            data=None if data is None else json.dumps(data).encode(),
        )
        with urlopen(request, timeout=8) as response:
            return json.load(response)

    def test_likes_work_for_admin_and_same_organization_posts(self):
        with server.db() as connection:
            connection.execute(
                "INSERT INTO community_admin_posts(admin_name,body,created_at) VALUES('Admin','Announcement',?)",
                (server.now(),),
            )
            connection.execute(
                "INSERT INTO community_posts(user_id,body,created_at) VALUES(1,'Organization post',?)",
                (server.now(),),
            )

        posts = self.customer('/api/community/posts')['items']
        admin_post = next(post for post in posts if post['name'] == 'إدارة خدوم')
        organization_post = next(post for post in posts if post['name'] == 'Org 1')
        self.assertLess(admin_post['id'], 0)

        for post in (admin_post, organization_post):
            first = self.customer(f"/api/community/posts/{post['id']}/like", 'POST', {})
            repeated = self.customer(f"/api/community/posts/{post['id']}/like", 'POST', {})
            self.assertTrue(first['liked'])
            self.assertEqual(first['likeCount'], 1)
            self.assertEqual(repeated['likeCount'], 1)

    def test_like_cannot_target_another_organizations_post(self):
        with server.db() as connection:
            connection.execute(
                "INSERT INTO users(id,organization_id,name,username,password_hash,password_salt,role,created_at) "
                "VALUES(2,2,'Other user','other','x','x','admin',?)",
                (server.now(),),
            )
            connection.execute(
                "INSERT INTO community_posts(user_id,body,created_at) VALUES(2,'Private post',?)",
                (server.now(),),
            )
        with self.assertRaises(HTTPError) as error:
            self.customer('/api/community/posts/1/like', 'POST', {})
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


for name in dir(AdminConsoleTest):
    if name.startswith('test_') and name not in CommunityLikesTest.__dict__:
        setattr(CommunityLikesTest, name, None)


if __name__ == '__main__':
    unittest.main()
